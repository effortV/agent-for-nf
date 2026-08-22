from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import ImportJob, JobStatus, KnowledgeBase, LiteratureAutomation
from app.services.pipeline import apply_control_checkpoint
from app.services.task_control import (
    CANCEL_REQUESTED,
    CANCELLED,
    PAUSE_REQUESTED,
    PAUSED,
    automation_is_deleted,
    get_job_control,
    mark_automation_deleted,
    normalize_interrupted_pause,
    request_job_control,
)


def make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def make_job(db) -> ImportJob:
    knowledge_base = KnowledgeBase(name="test")
    db.add(knowledge_base)
    db.flush()
    job = ImportJob(knowledge_base_id=knowledge_base.id, query="test", status=JobStatus.downloading)
    db.add(job)
    db.commit()
    return job


def test_pause_request_becomes_durable_paused_checkpoint() -> None:
    db = make_session()
    job = make_job(db)
    request_job_control(db, job, PAUSE_REQUESTED)
    db.commit()

    assert apply_control_checkpoint(db, job) == PAUSED
    assert get_job_control(db, job.id).state == PAUSED
    assert job.status == JobStatus.queued
    assert job.counts["execution_state"] == "paused"


def test_worker_restart_turns_pause_request_into_startable_pause() -> None:
    db = make_session()
    job = make_job(db)
    control = request_job_control(db, job, PAUSE_REQUESTED)
    db.commit()

    assert normalize_interrupted_pause(job, control) is True
    assert control.state == PAUSED
    assert job.status == JobStatus.queued
    assert job.stage == "已暂停；点击开始/继续任务"
    assert job.counts["execution_state"] == "paused"


def test_delete_request_cancels_task_but_keeps_import_job_audit_row() -> None:
    db = make_session()
    job = make_job(db)
    request_job_control(db, job, CANCEL_REQUESTED, deleted=True)
    db.commit()

    assert apply_control_checkpoint(db, job) == CANCELLED
    assert db.get(ImportJob, job.id) is not None
    assert job.deleted is True
    assert job.status == JobStatus.completed
    assert job.stage == "已取消；已入库文献保留"


def test_deleted_automation_marker_is_backward_compatible_json() -> None:
    automation = LiteratureAutomation(
        knowledge_base_id="kb",
        name="auto",
        query="nanofiltration",
        settings_json={"existing": True},
    )
    mark_automation_deleted(automation)

    assert automation_is_deleted(automation)
    assert automation.settings_json["existing"] is True
    assert automation.settings_json["deleted_at"]
