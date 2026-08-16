from pathlib import Path
import importlib.util
import os
import zipfile


def _load_manager(path: Path):
    spec = importlib.util.spec_from_file_location("hamspotter_manager_update_permission_test", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_update_handles_non_executable_upgrade_helper(tmp_path, monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    mod = _load_manager(repo / "tools" / "hamspotter_manager.py")
    root = tmp_path / "installed"
    root.mkdir(); (root / "data").mkdir(); (root / "backups").mkdir()
    (root / ".env").write_text("CALLSIGN=DL0TEST\n", encoding="utf-8")
    (root / "VERSION").write_text("1.13.4\n", encoding="utf-8")
    payload = tmp_path / "payload"
    (payload / "app").mkdir(parents=True)
    (payload / "app" / "placeholder.py").write_text("# test\n", encoding="utf-8")
    (payload / "VERSION").write_text("1.13.5\n", encoding="utf-8")
    (payload / "upgrade.sh").write_text("#!/usr/bin/env bash\nset -euo pipefail\ncd \"$(dirname \"$0\")\"\nexec ./upgrade_v1.13.5.sh\n", encoding="utf-8")
    (payload / "upgrade_v1.13.5.sh").write_text("#!/usr/bin/env bash\nset -euo pipefail\nprintf 'ran' > helper-ran.txt\n", encoding="utf-8")
    os.chmod(payload / "upgrade.sh", 0o644); os.chmod(payload / "upgrade_v1.13.5.sh", 0o644)
    archive = tmp_path / "update.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for p in payload.rglob("*"):
            if p.is_file(): zf.write(p, p.relative_to(payload))
    monkeypatch.setattr(mod, "ROOT", root); monkeypatch.setattr(mod, "ENV_FILE", root / ".env")
    monkeypatch.setattr(mod, "DATA_DIR", root / "data"); monkeypatch.setattr(mod, "BACKUP_DIR", root / "backups")
    monkeypatch.setattr(mod, "VERSION_FILE", root / "VERSION")
    monkeypatch.setattr(mod, "backup", lambda *a, **k: root / "backups" / "test.tar.gz")
    monkeypatch.setattr(mod, "restart", lambda: None); monkeypatch.setattr(mod, "healthcheck", lambda: True)
    mod.update(str(archive))
    assert (root / "helper-ran.txt").read_text(encoding="utf-8") == "ran"
    assert os.access(root / "upgrade.sh", os.X_OK)
    assert os.access(root / "upgrade_v1.13.5.sh", os.X_OK)


def test_release_wrapper_invokes_helper_through_bash():
    repo = Path(__file__).resolve().parents[1]
    wrapper = (repo / "upgrade.sh").read_text(encoding="utf-8")
    assert "exec bash ./upgrade_v" in wrapper


def test_wait_for_container_ready_waits_for_docker_healthy(monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    mod = _load_manager(repo / "tools" / "hamspotter_manager.py")
    states = iter(["starting", "starting", "healthy"])
    api_calls = []
    monkeypatch.setattr(mod, "_container_health_status", lambda: next(states))
    monkeypatch.setattr(mod, "_api", lambda path, timeout=5: api_calls.append(path) or {"ok": True})
    monkeypatch.setattr(mod.time, "sleep", lambda _seconds: None)
    mod._wait_for_container_ready(timeout=2, poll=0)
    assert api_calls == ["/health"]


def test_restart_waits_for_readiness_after_compose(monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    mod = _load_manager(repo / "tools" / "hamspotter_manager.py")
    events = []
    monkeypatch.setattr(mod, "heading", lambda _title: None)
    monkeypatch.setattr(mod, "compose", lambda args, check=True: events.append(("compose", args)))
    monkeypatch.setattr(mod, "_wait_for_container_ready", lambda: events.append(("wait", None)))
    mod.restart()
    assert events == [("compose", ["up", "-d", "--build"]), ("wait", None)]
