from pathlib import Path

import pytest

from backend.http.static_files import validate_production_web_dist
from turnstile_core.config import Settings


def test_production_requires_frontend_index(tmp_path: Path) -> None:
    settings = Settings(production=True, web_dist_dir=tmp_path / "missing")

    with pytest.raises(RuntimeError, match="WEB_DIST_DIR must contain index.html"):
        validate_production_web_dist(settings)


def test_production_accepts_frontend_index(tmp_path: Path) -> None:
    web_dist = tmp_path / "dist"
    web_dist.mkdir()
    (web_dist / "index.html").write_text("<!doctype html>", encoding="utf-8")

    validate_production_web_dist(Settings(production=True, web_dist_dir=web_dist))


def test_non_production_allows_missing_frontend_build(tmp_path: Path) -> None:
    validate_production_web_dist(
        Settings(production=False, web_dist_dir=tmp_path / "missing")
    )