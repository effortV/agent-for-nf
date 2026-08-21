from __future__ import annotations

import os
import runpy


# Community Cloud runs a single Python service and cannot start Docker Compose.
_CLOUD_DEFAULTS = {
    "ENVIRONMENT": "production",
    "API_BASE_URL": "http://127.0.0.1:8000",
    "CORS_ORIGINS": "*",
    "DATABASE_URL": "sqlite:///./data/runtime/nf_agent.db",
    "USE_RQ": "false",
    "STORAGE_BACKEND": "local",
    "STORAGE_ROOT": "./data/runtime/objects",
    "CHROMA_PATH": "./data/runtime/chroma",
    "ALLOW_EMBEDDING_DOWNLOAD": "false",
    "ALLOW_EMBEDDING_FALLBACK": "true",
    "GROBID_URL": "",
    "STREAMLIT_CLOUD_MODE": "true",
}
for _name, _value in _CLOUD_DEFAULTS.items():
    os.environ.setdefault(_name, _value)

from app.cloud_runtime import ensure_embedded_api  # noqa: E402


ensure_embedded_api()
runpy.run_module("ui.streamlit_app", run_name="__main__")
