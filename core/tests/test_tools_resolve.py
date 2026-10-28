import os

WS = os.environ["AYA_TEST_WS"]


def test_resolve_happy(db_reset):
    from core.safety import tools

    path = tools.resolve_allowed_path({"name": "a.md"})
    assert path.lower().startswith(WS.lower())
    assert path.endswith("a.md")
    assert os.path.isabs(path)


def test_resolve_blocks():
    from core.safety import tools

    for name in ("../x.md", "sub/dir.md", "bad\\name", "weird:colon", "..", ""):
        res = tools.resolve_allowed_path({"name": name})
        assert not os.path.isabs(res), name
        assert ("недопустимое" in res) or ("рабочая" in res)


def test_resolve_missing_folder(db_reset):
    import core.db as db
    from core.safety import tools

    db.set_setting("allowed_folder", "Z:/definitely-not-exist")
    res = tools.resolve_allowed_path({"name": "a.md"})
    assert "рабочая" in res
