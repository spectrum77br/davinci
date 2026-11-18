"""The launcher starts inside the container, where the code sits only three folders deep."""

from pathlib import Path

from app.erp_local.__main__ import local_data


def test_default_data_folder_in_the_repository_and_in_the_container(tmp_path, monkeypatch):
    deep = Path("/repo/apps/api/app/erp_local/__main__.py")
    assert local_data(deep) == Path("/repo/data/erp-local")
    monkeypatch.chdir(tmp_path)
    assert (
        local_data(Path("/app/app/erp_local/__main__.py")) == tmp_path.resolve() / "data/erp-local"
    )
