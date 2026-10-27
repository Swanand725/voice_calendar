from app.models import Item
from app import storage


def test_add_search_update_delete(tmp_path):
    f = tmp_path / "cal.json"           # temp file, your real data is never touched

    a = storage.add_item(Item(title="Call the dentist", date="2026-10-27", time="15:00"), f)
    storage.add_item(Item(title="Buy groceries", date="2026-10-27"), f)

    assert len(storage.list_items(date="2026-10-27", path=f)) == 2
    assert storage.search("dentist", path=f)[0].id == a.id

    storage.update_item(a.id, path=f, time="16:00")
    assert storage.get_item(a.id, f).time == "16:00"

    assert storage.delete_item(a.id, f) is True
    assert storage.delete_item(a.id, f) is False
    assert len(storage.list_items(path=f)) == 1