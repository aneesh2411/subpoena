"""Keep the FAQ in sync with the status enums it explains."""

from pathlib import Path

import pytest

from subpoena.auditor.resolution import Resolution
from subpoena.models import Outcome, VerdictStatus

FAQ = Path(__file__).resolve().parents[1] / "docs" / "FAQ.md"


@pytest.mark.parametrize("status_enum", [VerdictStatus, Outcome, Resolution])
def test_faq_documents_status_values(status_enum):
    faq = FAQ.read_text(encoding="utf-8")
    missing = [
        member.value
        for member in status_enum.__members__.values()
        if f"`{member.value}`" not in faq
    ]
    assert not missing, f"docs/FAQ.md is missing {status_enum.__name__} values: {missing}"
