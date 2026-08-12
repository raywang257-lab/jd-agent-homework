import json

from jd_agent import storage


def test_recent_events_are_isolated_by_run_id(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "audit.db")
    storage.log_event("jd_generated", "run-a", {"job": "A"})
    storage.log_event("jd_generated", "run-b", {"job": "B"})

    events = storage.recent_events("run-a")

    assert len(events) == 1
    assert events[0]["run_id"] == "run-a"
    assert json.loads(events[0]["metadata"])["job"] == "A"


def test_empty_run_id_never_returns_global_events(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "audit.db")
    storage.log_event("jd_generated", "run-a", {})
    assert storage.recent_events("") == []
