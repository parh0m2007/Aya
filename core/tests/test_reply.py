from core.reply import StreamFilter, detect_mood, parse_action


def run(chunks):
    f = StreamFilter()
    out = "".join(f.feed(c) for c in chunks) + f.flush()
    return out, f.hidden


def test_plain_text_streams_from_first_chunk():
    f = StreamFilter()
    assert f.feed("При") == "При"
    assert f.feed("вет!") == "вет!"
    assert not f.hidden


def test_single_line_reply_is_not_held_back():
    out, hidden = run(["Конечно", ", ", "сейчас всё сделаю."])
    assert out == "Конечно, сейчас всё сделаю." and not hidden


def test_action_json_is_hidden():
    out, hidden = run(['{"action":', '{"name":"open_app","args":{"app_id":"calc"}}}'])
    assert out == "" and hidden


def test_action_json_with_tail_and_fence():
    out, hidden = run(["```json\n", '{"action":{"name":"set_volume","args":{"level":30}}}', "\n```"])
    assert out == "" and hidden


def test_multiline_action_json_hidden():
    out, hidden = run(['{\n  "action": {\n', '    "name": "create_note",\n    "args": {"text": "x"}\n  }\n}'])
    assert out == "" and hidden


def test_brace_text_is_shown():
    out, hidden = run(["{хм} ", "это просто текст\nи ещё"])
    assert out.startswith("{хм}") and not hidden


def test_parse_action_with_trailing_text():
    action, tail = parse_action('{"action":{"name":"open_app","args":{"app_id":"calc"}}} Открываю!')
    assert action == {"name": "open_app", "args": {"app_id": "calc"}}
    assert tail == "Открываю!"


def test_parse_action_rejects_plain():
    assert parse_action("Привет")[0] is None
    assert parse_action('{"foo": 1}')[0] is None
    assert parse_action('{"action": {"args": {}}}')[0] is None


def test_detect_mood():
    assert detect_mood("Ура, получилось! Здорово!") == "joyful"
    assert detect_mood("Мне очень жаль, это грустно.") == "sad"
    assert detect_mood("Ого, ничего себе!") == "surprise"
    assert detect_mood("Хм, интересно, а почему?") == "thoughtful"
    assert detect_mood("Хорошо.") == "calm"
    assert detect_mood("") == ""


def test_detect_mood_short_words_whole():
    assert detect_mood("Того много, кого нет.") == "calm"
