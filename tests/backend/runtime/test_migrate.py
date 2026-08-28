from pathlib import Path

import pytest

from backend.migrate import migration_files


def test_migration_files_rejects_missing_directory(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="does not exist"):
        migration_files(tmp_path / "missing")


def test_migration_files_rejects_empty_directory(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="contains no up migrations"):
        migration_files(tmp_path)


def test_migration_files_discovers_only_sorted_up_migrations(tmp_path: Path) -> None:
    for name in (
        "002_second.up.sql",
        "001_first.up.sql",
        "001_first.down.sql",
        "notes.sql",
    ):
        (tmp_path / name).write_text("SELECT 1;", encoding="utf-8")

    assert [path.name for path in migration_files(tmp_path)] == [
        "001_first.up.sql",
        "002_second.up.sql",
    ]