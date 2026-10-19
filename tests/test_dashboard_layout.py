"""Grille du Dashboard : collisions, compaction, projection responsive."""

from __future__ import annotations

from UI.dashboard.layout_model import (
    GRID_COLUMNS,
    Page,
    Placement,
    clamp_rect,
    columns_for_width,
    find_free_slot,
    has_overlap,
    pack,
    reflow,
    settle,
    size_class_for_width,
)


def _p(i, x, y, w, h, t="a.b"):
    return Placement(f"w{i}", t, x, y, w, h)


def test_breakpoints():
    assert columns_for_width(600) == 4 and size_class_for_width(600) == "narrow"
    assert columns_for_width(1100) == 8 and size_class_for_width(1100) == "normal"
    assert columns_for_width(1600) == 12 and size_class_for_width(1600) == "wide"


def test_pack_removes_overlaps_and_compacts_upwards():
    items = [_p(1, 0, 0, 6, 3), _p(2, 0, 1, 6, 3), _p(3, 6, 10, 6, 2)]
    packed = pack(items)
    assert not has_overlap(packed)
    by_id = {p.id: p for p in packed}
    assert by_id["w2"].y == 3  # poussé sous w1
    assert by_id["w3"].y == 0  # remonté (gravité)


def test_pack_with_fixed_item_pushes_others():
    items = [_p(1, 0, 0, 6, 3), _p(2, 6, 0, 6, 3)]
    moved = items[1].copy()
    moved.x = 0
    packed = pack([items[0], moved], fixed_id="w2")
    by_id = {p.id: p for p in packed}
    assert (by_id["w2"].x, by_id["w2"].y) == (0, 0)
    assert by_id["w1"].y >= 3
    assert not has_overlap(packed)


def test_settle_never_overlaps_and_keeps_hidden_items():
    items = [_p(i, (i * 5) % 12, i, 5, 3) for i in range(8)]
    items.append(Placement("hidden", "a.b", 0, 0, 12, 12, hidden=True))
    result = settle(items, fixed_id="w3")
    assert not has_overlap(result)
    assert any(p.id == "hidden" and p.hidden for p in result)
    assert len(result) == len(items)


def test_find_free_slot_reading_order():
    items = [_p(1, 0, 0, 6, 3), _p(2, 6, 0, 6, 3)]
    assert find_free_slot(items, 4, 2) == (0, 3)
    assert find_free_slot([_p(1, 0, 0, 6, 3)], 4, 2) == (6, 0)


def test_clamp_respects_min_max_and_grid():
    assert clamp_rect(10, -2, 6, 1, GRID_COLUMNS, (4, 3), (8, 6)) == (6, 0, 6, 3)
    assert clamp_rect(0, 0, 20, 30, GRID_COLUMNS, (4, 3), (8, 6)) == (0, 0, 8, 6)


def test_reflow_projection_does_not_mutate_canonical_layout():
    items = [_p(1, 0, 0, 4, 3), _p(2, 4, 0, 5, 6), _p(3, 9, 0, 3, 6)]
    before = [p.rect() for p in items]
    narrow = reflow(items, 4)
    assert [p.rect() for p in items] == before
    assert not has_overlap(narrow)
    assert all(p.x + p.w <= 4 for p in narrow)
    normal = reflow(items, 8, {"a.b": (3, 3)})
    assert not has_overlap(normal)
    assert all(p.x + p.w <= 8 for p in normal)


def test_page_round_trip_with_extras():
    page = Page("x", "Titre", False, "◇", [_p(1, 0, 0, 4, 4)], {"meta": 1})
    raw = page.to_dict()
    again = Page.from_dict(raw, taken_ids=set())
    assert again.to_dict() == raw


def test_duplicate_instance_ids_are_renamed_not_dropped():
    taken: set[str] = set()
    raw = {"id": "p", "title": "P", "widgets": [{"id": "dup", "type": "a.b"}, {"id": "dup", "type": "a.c"}]}
    page = Page.from_dict(raw, taken_ids=taken)
    assert len(page.widgets) == 2
    assert page.widgets[0].id != page.widgets[1].id
