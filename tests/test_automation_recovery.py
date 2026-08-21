from types import SimpleNamespace

from app.models import JobStatus
from app.services.automation import _account_finished_job
from app.services.recovery import is_priority_import, is_retryable_database_error


def test_automation_import_is_accounted_exactly_once() -> None:
    automation = SimpleNamespace(imported_total=7)
    job = SimpleNamespace(status=JobStatus.completed, counts={"indexed": 5})

    _account_finished_job(automation, job)
    _account_finished_job(automation, job)

    assert automation.imported_total == 12
    assert job.counts["automation_accounted"] is True


def test_only_connection_state_errors_are_automatically_retried() -> None:
    assert is_retryable_database_error("psycopg.errors.DuplicatePreparedStatement")
    assert is_retryable_database_error("PendingRollbackError after failed flush")
    assert not is_retryable_database_error("Elsevier HTTP 401")
    assert not is_retryable_database_error("DeepSeek quota exhausted")


def test_user_fulltext_jobs_recover_to_priority_queue() -> None:
    assert is_priority_import(SimpleNamespace(counts={"priority": True}, query="anything"))
    assert is_priority_import(SimpleNamespace(counts={}, query="用户上传：paper"))
    assert is_priority_import(SimpleNamespace(counts={}, query="公开网址/DOI 导入：paper"))
    assert not is_priority_import(SimpleNamespace(counts={}, query="批量重新检查 metadata-only 公开全文"))
