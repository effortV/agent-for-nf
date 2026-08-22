from __future__ import annotations

import base64
import hashlib
import hmac
import io
import select
import socketserver
import threading
from dataclasses import dataclass

import paramiko


def host_key_sha256(key: paramiko.PKey) -> str:
    """Return an OpenSSH-style SHA256 host-key fingerprint."""

    digest = hashlib.sha256(key.asbytes()).digest()
    encoded = base64.b64encode(digest).decode("ascii").rstrip("=")
    return f"SHA256:{encoded}"


class _ExpectedFingerprintPolicy(paramiko.MissingHostKeyPolicy):
    def __init__(self, expected: str) -> None:
        self.expected = expected.strip()

    def missing_host_key(
        self,
        client: paramiko.SSHClient,
        hostname: str,
        key: paramiko.PKey,
    ) -> None:
        actual = host_key_sha256(key)
        if not hmac.compare_digest(actual, self.expected):
            raise paramiko.SSHException(
                f"SSH 主机指纹不匹配：期望 {self.expected}，实际 {actual}"
            )
        client.get_host_keys().add(hostname, key.get_name(), key)


def _load_private_key(value: str) -> paramiko.PKey:
    errors: list[str] = []
    for key_type in (paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.RSAKey):
        try:
            return key_type.from_private_key(io.StringIO(value.strip() + "\n"))
        except (paramiko.SSHException, ValueError) as exc:
            errors.append(str(exc))
    raise ValueError("无法读取 NF_SSH_PRIVATE_KEY；请粘贴完整的未加密 OpenSSH 私钥。" + " / ".join(errors[-2:]))


class _ForwardHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        server = self.server
        if not isinstance(server, _ForwardServer):
            return
        try:
            channel = server.transport.open_channel(
                "direct-tcpip",
                (server.remote_host, server.remote_port),
                self.request.getpeername(),
            )
        except Exception:
            return
        if channel is None:
            return
        try:
            while True:
                readable, _, _ = select.select([self.request, channel], [], [], 2.0)
                if self.request in readable:
                    data = self.request.recv(65536)
                    if not data:
                        break
                    channel.sendall(data)
                if channel in readable:
                    data = channel.recv(65536)
                    if not data:
                        break
                    self.request.sendall(data)
        finally:
            channel.close()


class _ForwardServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        transport: paramiko.Transport,
        remote_host: str,
        remote_port: int,
    ) -> None:
        self.transport = transport
        self.remote_host = remote_host
        self.remote_port = remote_port
        super().__init__(server_address, _ForwardHandler)


@dataclass
class SSHTunnel:
    client: paramiko.SSHClient
    server: _ForwardServer
    thread: threading.Thread

    @property
    def local_port(self) -> int:
        return int(self.server.server_address[1])

    @property
    def is_alive(self) -> bool:
        transport = self.client.get_transport()
        return bool(transport and transport.is_active() and self.thread.is_alive())

    @classmethod
    def connect(
        cls,
        *,
        ssh_host: str,
        ssh_port: int,
        ssh_username: str,
        private_key: str,
        host_key_fingerprint: str,
        remote_host: str = "127.0.0.1",
        remote_port: int = 8000,
    ) -> SSHTunnel:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(_ExpectedFingerprintPolicy(host_key_fingerprint))
        client.connect(
            hostname=ssh_host,
            port=ssh_port,
            username=ssh_username,
            pkey=_load_private_key(private_key),
            look_for_keys=False,
            allow_agent=False,
            timeout=20,
            banner_timeout=20,
            auth_timeout=20,
        )
        transport = client.get_transport()
        if transport is None or not transport.is_active():
            client.close()
            raise RuntimeError("SSH 已认证，但转发通道未建立。")
        transport.set_keepalive(30)
        server = _ForwardServer(("127.0.0.1", 0), transport, remote_host, remote_port)
        thread = threading.Thread(
            target=server.serve_forever,
            name="nf-atlas-streamlit-ssh-tunnel",
            daemon=True,
        )
        thread.start()
        return cls(client=client, server=server, thread=thread)

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.client.close()
