from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.db import Base
from app.models import LocalLibraryItem
from app.services.local_library import LocalLibraryCatalog


def test_zotero_catalog_remaps_windows_storage_path_and_searches_fulltext(tmp_path) -> None:
    pdf = tmp_path / "storage" / "ABCD1234" / "paper.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF-1.4\n")
    catalog = tmp_path / "AI4Membrane library.csv"
    catalog.write_text(
        'Key,Title,Author,DOI,Publication Year,Abstract Note,File Attachments\n'
        'ABCD1234,"Machine learning prediction for nanofiltration membranes",'
        '"Zhang, San;Li, Si",10.1000/nfml,2025,"neural network flux prediction",'
        '"E:\\storage\\ABCD1234\\paper.pdf"\n',
        encoding="utf-8",
    )
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    service = LocalLibraryCatalog(Settings(local_library_root=tmp_path, local_library_auto_sync=True))

    stats = service.sync(db, force=True)
    results = service.search(db, "machine learning nanofiltration", {}, limit=10)

    assert stats["indexed_records"] == 1
    assert stats["fulltext_records"] == 1
    assert db.scalar(select(LocalLibraryItem)).attachment_path == "storage/ABCD1234/paper.pdf"
    assert results[0].source == "local-library"
    assert results[0].raw["local_library_path"] == "storage/ABCD1234/paper.pdf"

