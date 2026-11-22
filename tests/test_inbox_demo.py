"""The inbox export matches the committed demo page."""

from pathlib import Path

from scripts.export_inbox_demo import DEFAULT_OUT, export


def test_inbox_export_is_byte_reproducible(tmp_path: Path) -> None:
    expected = (DEFAULT_OUT / "inbox.html").read_bytes()
    for out in (tmp_path / "first", tmp_path / "second"):
        export(out)
        assert (out / "inbox.html").read_bytes() == expected

    page = expected.decode("utf-8")
    before, template = page.split('<template id="claim-filter-template">')
    note, after = template.split("</template>")
    assert 'id="claim-filter"' in note
    assert 'id="claim-filter"' not in before + after
