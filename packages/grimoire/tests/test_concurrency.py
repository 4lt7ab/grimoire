import sqlite3
import threading

from grimoire.data.entry import Entry
from grimoire.grimoire import Grimoire


def test_check_same_thread_false_allows_use_from_another_thread(
    tmp_path, fake_embedder
):
    db = tmp_path / "g.db"
    with Grimoire.open(db, embedder=fake_embedder, check_same_thread=False) as g:
        result = {}

        def work():
            [e] = g.add([Entry(None, {"k": "v"})])
            result["entries"] = g.get([e.uniq_id])

        t = threading.Thread(target=work)
        t.start()
        t.join()

        assert result["entries"][0].data == {"k": "v"}


def test_default_rejects_use_from_another_thread(tmp_path, fake_embedder):
    db = tmp_path / "g.db"
    with Grimoire.open(db, embedder=fake_embedder) as g:
        errors = []

        def work():
            try:
                g.add([Entry(None, {"k": "v"})])
            except Exception as exc:
                errors.append(exc)

        t = threading.Thread(target=work)
        t.start()
        t.join()

        assert isinstance(errors[0], sqlite3.ProgrammingError)
