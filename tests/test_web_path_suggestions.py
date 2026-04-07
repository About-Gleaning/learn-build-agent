from pathlib import Path

from agent.runtime.workspace import configure_workspace, reset_workspace
from agent.web.path_suggestions import record_path_selection, suggest_workspace_paths


def test_suggest_workspace_paths_should_match_across_workspace(tmp_path: Path):
    (tmp_path / "alpha").mkdir()
    (tmp_path / "alpha" / "test-file.py").write_text("print('x')", encoding="utf-8")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "beta-test-dir").mkdir()

    configure_workspace(tmp_path, launch_mode="web")
    try:
        results = suggest_workspace_paths("test")
    finally:
        reset_workspace()

    relative_paths = [item.relative_path for item in results]
    assert "alpha/test-file.py" in relative_paths
    assert "nested/beta-test-dir" in relative_paths


def test_suggest_workspace_paths_should_return_empty_list_for_empty_query(tmp_path: Path):
    configure_workspace(tmp_path, launch_mode="web")
    try:
        assert suggest_workspace_paths("") == []
        assert suggest_workspace_paths("   ") == []
    finally:
        reset_workspace()


def test_suggest_workspace_paths_should_prioritize_more_direct_and_contiguous_matches(tmp_path: Path):
    (tmp_path / "abc.txt").write_text("a", encoding="utf-8")
    (tmp_path / "abx.txt").write_text("b", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "abc_service.ts").write_text("c", encoding="utf-8")
    (tmp_path / "src" / "a_b_c_util.ts").write_text("d", encoding="utf-8")

    configure_workspace(tmp_path, launch_mode="web")
    try:
        results = suggest_workspace_paths("abc")
    finally:
        reset_workspace()

    assert [item.relative_path for item in results[:3]] == [
        "abc.txt",
        "src/abc_service.ts",
        "src/a_b_c_util.ts",
    ]


def test_suggest_workspace_paths_should_sort_same_score_by_relative_path(tmp_path: Path):
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "file.ts").write_text("b", encoding="utf-8")
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "file.ts").write_text("a", encoding="utf-8")

    configure_workspace(tmp_path, launch_mode="web")
    try:
        results = suggest_workspace_paths("file")
    finally:
        reset_workspace()

    assert [item.relative_path for item in results[:2]] == ["a/file.ts", "b/file.ts"]


def test_suggest_workspace_paths_should_use_match_score_before_path_tiebreaker(tmp_path: Path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "user-guide.md").write_text("# guide", encoding="utf-8")
    (tmp_path / "modules").mkdir()
    (tmp_path / "modules" / "user_service.ts").write_text("export {}", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "account").mkdir()
    (tmp_path / "src" / "account" / "current_user").mkdir()
    (tmp_path / "src" / "account" / "current_user" / "profile.ts").write_text("export {}", encoding="utf-8")

    configure_workspace(tmp_path, launch_mode="web")
    try:
        results = suggest_workspace_paths("user")
    finally:
        reset_workspace()

    relative_paths = [item.relative_path for item in results]
    assert "docs/user-guide.md" in relative_paths[:2]
    assert "modules/user_service.ts" in relative_paths[:2]
    assert relative_paths.index("src/account/current_user/profile.ts") > relative_paths.index("docs/user-guide.md")
    assert relative_paths.index("src/account/current_user/profile.ts") > relative_paths.index("modules/user_service.ts")


def test_suggest_workspace_paths_should_return_empty_list_when_no_match(tmp_path: Path):
    (tmp_path / "alpha.txt").write_text("a", encoding="utf-8")

    configure_workspace(tmp_path, launch_mode="web")
    try:
        assert suggest_workspace_paths("zzz_not_found") == []
    finally:
        reset_workspace()


def test_suggest_workspace_paths_should_ignore_recent_selection_for_sorting(tmp_path: Path):
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "file.ts").write_text("b", encoding="utf-8")
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "file.ts").write_text("a", encoding="utf-8")

    configure_workspace(tmp_path, launch_mode="web")
    try:
        record_path_selection("b/file.ts")
        results = suggest_workspace_paths("file")
    finally:
        reset_workspace()

    assert [item.relative_path for item in results[:2]] == ["a/file.ts", "b/file.ts"]


def test_record_path_selection_should_reject_workspace_escape(tmp_path: Path):
    configure_workspace(tmp_path, launch_mode="web")
    try:
        try:
            record_path_selection("../outside.txt")
        except ValueError as exc:
            assert str(exc) == "relative_path 超出工作区范围"
        else:  # pragma: no cover
            raise AssertionError("expected ValueError")
    finally:
        reset_workspace()
