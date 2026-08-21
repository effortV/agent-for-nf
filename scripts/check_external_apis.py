from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.services.literature import LiteratureDiscovery
from app.services.llm import DeepSeekClient


async def main() -> None:
    settings = get_settings()
    report: dict[str, object] = {}

    if settings.siliconflow_api_key:
        try:
            answer = await DeepSeekClient(settings).chat(
                [
                    {"role": "system", "content": "You are a connectivity check."},
                    {"role": "user", "content": "Reply with exactly: OK"},
                ],
                temperature=0,
                max_tokens=8,
            )
            report["siliconflow"] = {"ok": bool(answer.strip()), "model": settings.llm_model}
        except Exception as exc:
            report["siliconflow"] = {
                "ok": False,
                "error": {"type": type(exc).__name__, "status_code": getattr(exc, "status_code", None)},
            }
    else:
        report["siliconflow"] = {"ok": False, "error": "SILICONFLOW_API_KEY is missing"}

    discovery = LiteratureDiscovery(settings)
    try:
        if settings.elsevier_api_key:
            try:
                rows = await discovery._search_elsevier("nanofiltration membrane", 1, None, None)
                report["elsevier"] = {"ok": True, "result_count": len(rows)}
            except Exception as exc:
                report["elsevier"] = {"ok": False, "error": discovery._safe_error(exc)}
        else:
            report["elsevier"] = {"ok": False, "error": "ELSEVIER_API_KEY is missing"}

        if settings.openalex_api_key:
            try:
                rows = await discovery._search_openalex("nanofiltration membrane", 1, None, None)
                report["openalex"] = {"ok": True, "result_count": len(rows)}
            except Exception as exc:
                report["openalex"] = {"ok": False, "error": discovery._safe_error(exc)}
        else:
            report["openalex"] = {"ok": False, "error": "OPENALEX_API_KEY is missing"}
    finally:
        await discovery.close()

    if settings.unpaywall_email:
        async with httpx.AsyncClient(timeout=20) as client:
            try:
                response = await client.get(
                    "https://api.unpaywall.org/v2/10.1038/nature12373",
                    params={"email": settings.unpaywall_email},
                )
                response.raise_for_status()
                report["unpaywall"] = {"ok": True, "is_oa": bool(response.json().get("is_oa"))}
            except Exception as exc:
                report["unpaywall"] = {"ok": False, "error": LiteratureDiscovery._safe_error(exc)}
    else:
        report["unpaywall"] = {"ok": False, "error": "UNPAYWALL_EMAIL is missing"}
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
