from __future__ import annotations

from codeledger_core.repo_map import RepoMapBuilder


def test_repo_map_finds_python_symbols(tmp_path) -> None:
    package = tmp_path / "pkg"
    package.mkdir()

    (package / "a.py").write_text(
        "class A:\n"
        "    pass\n"
        "\n"
        "def f():\n"
        "    pass\n",
        encoding="utf-8",
    )

    (package / "b.py").write_text(
        "def g():\n"
        "    pass\n",
        encoding="utf-8",
    )

    repo_map = RepoMapBuilder().build(tmp_path)
    rendered = repo_map.render()

    assert len(repo_map.files) == 2
    assert "pkg/a.py: class A, def f" in rendered
    assert "pkg/b.py: def g" in rendered


def test_repo_map_skips_ignored_dirs(tmp_path) -> None:
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "ignored.py").write_text(
        "def hidden():\n    pass\n",
        encoding="utf-8",
    )

    repo_map = RepoMapBuilder().build(tmp_path)

    assert all(".venv" not in file.path for file in repo_map.files)


def test_repo_map_truncates(tmp_path) -> None:
    for index in range(5):
        (tmp_path / f"file_{index}.py").write_text(
            f"def f{index}():\n    pass\n",
            encoding="utf-8",
        )

    repo_map = RepoMapBuilder(max_files=3).build(tmp_path)

    assert len(repo_map.files) == 3
    assert repo_map.truncated is True