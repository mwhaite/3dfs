from three_dfs.ui.container_pane import _is_readme_filename


def test_is_readme_filename_accepts_markdown_files() -> None:
    assert _is_readme_filename("README.md")
    assert _is_readme_filename("/tmp/foo/ReadMe.MD")
    assert _is_readme_filename("docs/readme.txt")


def test_is_readme_filename_rejects_other_files() -> None:
    assert not _is_readme_filename("notes.md")
    assert not _is_readme_filename("readme_backup.md.bak")
    assert not _is_readme_filename("")
