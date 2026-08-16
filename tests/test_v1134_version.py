from pathlib import Path


def test_docker_image_includes_repository_version_file():
    root = Path(__file__).resolve().parents[1]
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY VERSION ./VERSION" in dockerfile


def test_application_reads_repository_version_file():
    root = Path(__file__).resolve().parents[1]
    source = (root / "app" / "main.py").read_text(encoding="utf-8")
    assert 'Path(__file__).resolve().parents[1] / "VERSION"' in source


def test_release_version_is_semver():
    import re
    root = Path(__file__).resolve().parents[1]
    version = (root / "VERSION").read_text(encoding="utf-8").strip()
    assert re.fullmatch(r"\d+\.\d+\.\d+", version)
