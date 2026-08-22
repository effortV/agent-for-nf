from __future__ import annotations

import io

import paramiko
import pytest

from ui.ssh_tunnel import _ExpectedFingerprintPolicy, _load_private_key, host_key_sha256


def test_private_key_and_fingerprint_round_trip() -> None:
    key = paramiko.RSAKey.generate(1024)
    buffer = io.StringIO()
    key.write_private_key(buffer)

    loaded = _load_private_key(buffer.getvalue())

    assert loaded.get_base64() == key.get_base64()
    assert host_key_sha256(loaded).startswith("SHA256:")


def test_host_key_policy_rejects_unexpected_key() -> None:
    expected = paramiko.RSAKey.generate(1024)
    actual = paramiko.RSAKey.generate(1024)
    policy = _ExpectedFingerprintPolicy(host_key_sha256(expected))

    with pytest.raises(paramiko.SSHException, match="SSH 主机指纹不匹配"):
        policy.missing_host_key(paramiko.SSHClient(), "example.test", actual)
