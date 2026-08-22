from __future__ import annotations

import os
import runpy
from typing import Any

import streamlit as st

from ui.ssh_tunnel import SSHTunnel

st.set_page_config(page_title="NF-Atlas 纳滤智能体", page_icon="🧪", layout="wide")
os.environ["STREAMLIT_PAGE_CONFIGURED"] = "true"


def _secret(name: str, default: Any = None) -> Any:
    try:
        return st.secrets.get(name, os.getenv(name, default))
    except FileNotFoundError:
        return os.getenv(name, default)


@st.cache_resource(show_spinner="正在连接服务器主库……")
def _open_tunnel(
    ssh_host: str,
    ssh_port: int,
    ssh_username: str,
    private_key: str,
    host_key_fingerprint: str,
    remote_host: str,
    remote_port: int,
) -> SSHTunnel:
    return SSHTunnel.connect(
        ssh_host=ssh_host,
        ssh_port=ssh_port,
        ssh_username=ssh_username,
        private_key=private_key,
        host_key_fingerprint=host_key_fingerprint,
        remote_host=remote_host,
        remote_port=remote_port,
    )


def _configure_remote_backend() -> None:
    """Connect Community Cloud to the persistent server; never start local storage."""

    os.environ["STREAMLIT_CLOUD_MODE"] = "true"
    os.environ["STREAMLIT_REMOTE_BACKEND"] = "true"

    direct_url = str(_secret("NF_BACKEND_URL", "") or "").strip().rstrip("/")
    if direct_url:
        allow_insecure = str(_secret("NF_ALLOW_INSECURE_BACKEND", "false")).casefold() in {
            "1",
            "true",
            "yes",
            "on",
        }
        if not direct_url.startswith("https://") and not allow_insecure:
            raise RuntimeError("NF_BACKEND_URL 必须使用 HTTPS；如无域名，请使用下方 SSH 转发配置。")
        os.environ["API_BASE_URL"] = direct_url
    else:
        required = {
            "NF_SSH_HOST": _secret("NF_SSH_HOST"),
            "NF_SSH_USERNAME": _secret("NF_SSH_USERNAME"),
            "NF_SSH_PRIVATE_KEY": _secret("NF_SSH_PRIVATE_KEY"),
            "NF_SSH_HOST_KEY_SHA256": _secret("NF_SSH_HOST_KEY_SHA256"),
        }
        missing = [name for name, value in required.items() if not str(value or "").strip()]
        if missing:
            raise RuntimeError(
                "Streamlit Secrets 尚未配置服务器主库。缺少：" + ", ".join(missing)
            )
        tunnel = _open_tunnel(
            str(required["NF_SSH_HOST"]),
            int(_secret("NF_SSH_PORT", 22)),
            str(required["NF_SSH_USERNAME"]),
            str(required["NF_SSH_PRIVATE_KEY"]),
            str(required["NF_SSH_HOST_KEY_SHA256"]),
            str(_secret("NF_REMOTE_API_HOST", "127.0.0.1")),
            int(_secret("NF_REMOTE_API_PORT", 8000)),
        )
        if not tunnel.is_alive:
            _open_tunnel.clear()
            raise RuntimeError("服务器 SSH 通道已断开，请重新唤醒应用。")
        os.environ["API_BASE_URL"] = f"http://127.0.0.1:{tunnel.local_port}"

    for name in (
        "NF_API_ACCESS_TOKEN",
        "CF_ACCESS_CLIENT_ID",
        "CF_ACCESS_CLIENT_SECRET",
    ):
        value = str(_secret(name, "") or "").strip()
        if value:
            os.environ[name] = value


try:
    _configure_remote_backend()
except Exception as exc:
    st.error(f"无法连接 NF-Atlas 服务器主库：{exc}")
    st.info("请在 Streamlit Cloud → App settings → Secrets 填入仓库示例中的服务器连接配置。")
    st.stop()

runpy.run_module("ui.streamlit_app", run_name="__main__")
