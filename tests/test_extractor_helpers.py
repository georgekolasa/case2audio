from types import SimpleNamespace

from case2audio.extractor import _is_text_table, _linearize_table, _table_title


def _table(rows):
    grid = [[SimpleNamespace(text=cell) for cell in row] for row in rows]
    return SimpleNamespace(
        data=SimpleNamespace(
            grid=grid,
            num_rows=len(grid),
            num_cols=max(len(row) for row in grid),
        )
    )


def test_smart_mode_recognizes_and_narrates_compact_text_table() -> None:
    table = _table(
        [
            ["1.", "Understands the firm's goals", "____"],
            ["2.", "Works on priority items", "____"],
        ]
    )

    assert _is_text_table(table)
    assert _linearize_table(table) == (
        "Table contents. 1. Understands the firm's goals. 2. Works on priority items."
    )


def test_dense_table_uses_its_merged_first_cell_as_title() -> None:
    table = _table(
        [
            ["Ten-Year Financial Summary"] * 5,
            ["Sales", "1", "2", "3", "4"],
        ]
    )

    assert not _is_text_table(table)
    assert _table_title(table) == "Ten-Year Financial Summary"
