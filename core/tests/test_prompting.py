from core import prompting


def test_build_history_short_and_summary(db_reset):
    import core.db as db

    for i in range(12):
        db.add_message("user", f"вопрос номер {i}")
        db.add_message("assistant", f"ответ {i}")

    h = prompting.build_history(verbatim=6, take=30)
    roles = [m["role"] for m in h]
    assert roles.count("user") == 3  # последние 6 = 3 user + 3 assistant
    assert h[0]["role"] == "assistant"
    assert "более старом" in h[0]["content"]
    assert "вопрос номер 5" in h[0]["content"]  # хвост стфи разговора в резюме
    assert "вопрос номер 11" not in h[0]["content"]  # не дублирует вербатим
    assert h[-1]["content"] == "ответ 11"


def test_build_history_filter_initiative(db_reset):
    import core.db as db

    db.add_message("event", "initiative:welcome_back")
    db.add_message("user", "привет")
    h = prompting.build_history(verbatim=8, take=30)
    assert len(h) == 1
    assert h[0]["role"] == "user"
    assert not (h and "initiative" in h[0]["content"])
