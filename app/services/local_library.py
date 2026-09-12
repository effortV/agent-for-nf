from __future__ import annotations

import csv
import hashlib
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import LocalLibraryItem
from app.services.dedupe import normalize_doi, title_author_fingerprint
from app.services.literature import LiteratureCandidate


def _authors(value: str | None) -> list[dict[str, Any]]:
    return [{"name": item.strip()} for item in (value or "").split(";") if item.strip()]


def _year(value: str | None) -> int | None:
    match = re.search(r"(?:18|19|20|21)\d{2}", value or "")
    return int(match.group(0)) if match else None


class LocalLibraryCatalog:
    """Catalogue and search a read-only Zotero export without copying every PDF."""

    source_name = "AI4Membrane lib"

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    @property
    def root(self) -> Path | None:
        return self.settings.local_library_root

    @property
    def catalog_path(self) -> Path | None:
        return self.root / self.settings.local_library_catalog if self.root else None

    @property
    def configured(self) -> bool:
        return bool(self.root and self.catalog_path and self.catalog_path.is_file())

    def resolve_attachment(self, raw: str | None) -> Path | None:
        if not self.root or not raw:
            return None
        root = self.root.resolve()
        for value in raw.split(";"):
            value = value.strip()
            marker = re.search(r"[\\/]storage[\\/](.+)$", value, re.I)
            relative = Path("storage") / Path(marker.group(1).replace("\\", "/")) if marker else Path(value)
            candidate = relative if relative.is_absolute() else root / relative
            try:
                resolved = candidate.resolve()
                resolved.relative_to(root)
            except (OSError, ValueError):
                continue
            if resolved.is_file() and resolved.suffix.casefold() in {".pdf", ".xml", ".html", ".htm"}:
                return resolved
        return None

    def sync(self, db: Session, *, force: bool = False) -> dict[str, Any]:
        if not self.configured or not self.catalog_path:
            return self.stats(db)
        existing_count = db.scalar(
            select(func.count(LocalLibraryItem.id)).where(LocalLibraryItem.source_name == self.source_name)
        ) or 0
        if existing_count and not force:
            return self.stats(db)

        existing = {
            item.source_key: item
            for item in db.scalars(select(LocalLibraryItem).where(LocalLibraryItem.source_name == self.source_name))
        }
        synced_at = datetime.now(UTC).isoformat()
        seen: set[str] = set()
        with self.catalog_path.open("r", encoding="utf-8-sig", newline="") as handle:
            for index, raw in enumerate(csv.DictReader(handle), 1):
                title = str(raw.get("Title") or "").strip()
                if not title:
                    continue
                authors = _authors(raw.get("Author"))
                source_key = str(raw.get("Key") or "").strip() or hashlib.sha256(
                    f"{title}|{raw.get('Author') or ''}".encode()
                ).hexdigest()[:24]
                seen.add(source_key)
                attachment = self.resolve_attachment(raw.get("File Attachments"))
                relative = attachment.relative_to(self.root.resolve()).as_posix() if attachment and self.root else None
                item = existing.get(source_key)
                values = {
                    "doi": normalize_doi(raw.get("DOI")),
                    "doi_normalized": normalize_doi(raw.get("DOI")),
                    "title": title,
                    "title_author_fingerprint": title_author_fingerprint(title, authors),
                    "authors": authors,
                    "abstract": str(raw.get("Abstract Note") or "").strip() or None,
                    "publication_year": _year(raw.get("Publication Year") or raw.get("Date")),
                    "venue": str(raw.get("Publication Title") or "").strip() or None,
                    "landing_url": str(raw.get("Url") or "").strip() or None,
                    "attachment_path": relative,
                    "attachment_exists": attachment is not None,
                    "metadata_json": {
                        "item_type": raw.get("Item Type"),
                        "rights": raw.get("Rights"),
                        "catalog_synced_at": synced_at,
                    },
                }
                if item:
                    for key, value in values.items():
                        setattr(item, key, value)
                else:
                    db.add(LocalLibraryItem(source_name=self.source_name, source_key=source_key, **values))
                if index % 500 == 0:
                    db.flush()
        for key, item in existing.items():
            if key not in seen:
                item.attachment_exists = False
                item.metadata_json = {**(item.metadata_json or {}), "missing_from_latest_catalog": True}
        db.commit()
        return self.stats(db)

    def ensure_synced(self, db: Session) -> dict[str, Any]:
        return self.sync(db) if self.settings.local_library_auto_sync else self.stats(db)

    def stats(self, db: Session) -> dict[str, Any]:
        source_filter = LocalLibraryItem.source_name == self.source_name
        total = db.scalar(select(func.count(LocalLibraryItem.id)).where(source_filter)) or 0
        fulltext = (
            db.scalar(
                select(func.count(LocalLibraryItem.id)).where(
                    source_filter,
                    LocalLibraryItem.attachment_exists.is_(True),
                )
            )
            or 0
        )
        latest = db.scalar(select(func.max(LocalLibraryItem.updated_at)).where(source_filter))
        return {
            "configured": self.configured,
            "root": str(self.root) if self.root else None,
            "catalog_exists": bool(self.catalog_path and self.catalog_path.is_file()),
            "indexed_records": total,
            "fulltext_records": fulltext,
            "missing_attachments": total - fulltext,
            "last_sync_at": latest,
        }

    def search(
        self,
        db: Session,
        query: str,
        expanded: dict[str, list[str]],
        *,
        limit: int,
        year_from: int | None = None,
        year_to: int | None = None,
    ) -> list[LiteratureCandidate]:
        if not self.configured:
            return []
        self.ensure_synced(db)
        original_terms = [term.casefold() for term in re.findall(r"[A-Za-z][A-Za-z0-9+./-]{1,40}|[\u4e00-\u9fff]{2,12}", query)]
        expanded_terms = [
            str(term).strip().casefold()
            for values in expanded.values()
            for term in values
            if 2 <= len(str(term).strip()) <= 80
        ]
        terms = list(dict.fromkeys([*original_terms, *expanded_terms]))[:120]
        rows = db.scalars(
            select(LocalLibraryItem).where(
                LocalLibraryItem.source_name == self.source_name,
                LocalLibraryItem.attachment_exists.is_(True),
            )
        )
        ranked: list[tuple[float, LocalLibraryItem, list[str]]] = []
        phrase = query.strip().casefold()
        for item in rows:
            if year_from and (not item.publication_year or item.publication_year < year_from):
                continue
            if year_to and (not item.publication_year or item.publication_year > year_to):
                continue
            title = item.title.casefold()
            abstract = (item.abstract or "").casefold()
            score = 12.0 if phrase and phrase in f"{title} {abstract}" else 0.0
            reasons: list[str] = []
            for term in terms:
                if term in title:
                    score += 4.0 + min(len(term), 24) / 12
                    reasons.append(f"题名命中：{term}")
                elif term in abstract:
                    score += 1.0 + min(len(term), 24) / 24
                    reasons.append(f"摘要命中：{term}")
            if score <= 0:
                continue
            ranked.append((score, item, list(dict.fromkeys(reasons))[:8]))
        ranked.sort(key=lambda value: (value[0], value[1].publication_year or 0), reverse=True)
        output: list[LiteratureCandidate] = []
        for score, item, reasons in ranked[: min(limit, self.settings.local_library_search_limit)]:
            candidate = LiteratureCandidate.create(
                source="local-library",
                title=item.title,
                authors=item.authors,
                doi=item.doi_normalized,
                publication_year=item.publication_year,
                venue=item.venue,
                abstract=item.abstract,
                landing_url=item.landing_url,
                license="user-provided-library",
                is_open_access=False,
                raw={
                    "local_library_item_id": item.id,
                    "local_library_key": item.source_key,
                    "local_library_path": item.attachment_path,
                },
            )
            candidate.relevance_score = round(score + 20.0, 5)
            candidate.relevance_reasons = ["本地全文库优先", *reasons]
            output.append(candidate)
        return output
