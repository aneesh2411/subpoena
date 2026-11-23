"""Sealed reports detect tampering; rendered reports say what happened."""

from __future__ import annotations

from random import Random

from subpoena.auditor import GENESIS, NaiveAuditor, Sealer, render_markdown
from subpoena.auditor.seal import Integrity, canonical_bytes
from subpoena.models import CLAIM_TYPES, AuditReport, Outcome, VerdictStatus
from subpoena.scenarios import BY_ID, HERO, run_scenario
from subpoena.scenarios.world import CLINIC

KEY = b"test-signing-key"


def _hero(sealer: Sealer | None = None) -> AuditReport:
    return run_scenario(BY_ID[HERO], sealer=sealer).report


def test_unsigned_seal_verifies_and_survives_a_json_round_trip():
    report = _hero()
    assert report.seal is not None and report.seal.signature is None
    assert report.seal.prev_hash == GENESIS
    restored = AuditReport.model_validate_json(report.model_dump_json())
    check = Sealer().verify(restored)
    assert check.ok and "unsigned" in check.reason


def test_canonical_payload_is_independent_of_dict_insertion_order(monkeypatch):
    sealer = Sealer(KEY)
    reports = {
        scenario_id: run_scenario(BY_ID[scenario_id], sealer=sealer).report
        for scenario_id in (
            "clean-book",
            "clean-reschedule",
            "clean-cancel",
            "clean-handoff-topic",
            "clean-payment",
            "clean-records-update",
            "clean-prescription-refill",
        )
    }
    assert {
        verdict.claim.type for report in reports.values() for verdict in report.verdicts
    } == set(CLAIM_TYPES)
    original_bytes = {
        scenario_id: canonical_bytes(report) for scenario_id, report in reports.items()
    }

    def shuffle_keys(value, rng):
        if isinstance(value, dict):
            keys = list(value)
            rng.shuffle(keys)
            if keys == list(value):
                keys.reverse()
            return {key: shuffle_keys(value[key], rng) for key in keys}
        if isinstance(value, list):
            return [shuffle_keys(item, rng) for item in value]
        return value

    # Shuffle the serializer's output directly. Revalidating shuffled input
    # would restore Pydantic's field order before canonical_bytes sees it.
    model_dump = AuditReport.model_dump
    rng = Random(341)

    def shuffled_dump(report, **kwargs):
        return shuffle_keys(model_dump(report, **kwargs), rng)

    monkeypatch.setattr(AuditReport, "model_dump", shuffled_dump)
    for _ in range(10):
        for scenario_id, report in reports.items():
            assert canonical_bytes(report) == original_bytes[scenario_id], scenario_id
            check = sealer.verify(report)
            assert check.integrity is Integrity.INTACT and check.ok, (scenario_id, check)


def test_any_edit_to_a_sealed_report_is_detected():
    report = _hero(Sealer(KEY))
    softened = report.verdicts[0].model_copy(update={"status": VerdictStatus.VERIFIED})
    for tampered in (
        report.model_copy(update={"outcome": Outcome.CLEAN}),
        report.model_copy(update={"verdicts": (softened, *report.verdicts[1:])}),
        report.model_copy(update={"findings": ()}) if report.findings else None,
        report.model_copy(update={"trail": report.trail[:-1]}),
    ):
        if tampered is not None:
            check = Sealer(KEY).verify(tampered)
            assert not check.ok and "altered" in check.reason


def test_signature_requires_the_right_key():
    report = _hero(Sealer(KEY))
    assert Sealer(KEY).verify(report).ok
    assert Sealer(KEY).key_id == report.seal.key_id
    wrong = Sealer(b"another-key").verify(report)
    assert not wrong.ok and "signature" in wrong.reason
    keyless = Sealer().verify(report)
    assert keyless.ok and "not checked" in keyless.reason
    assert not Sealer(KEY).verify(_hero()).ok  # unsigned report, key expected


def test_forged_seal_with_recomputed_hashes_fails_without_the_key():
    report = _hero(Sealer(KEY))
    forged = Sealer().seal(report.model_copy(update={"outcome": Outcome.CLEAN}))
    assert Sealer().verify(forged).ok  # hashes are self-consistent...
    assert not Sealer(KEY).verify(forged).ok  # ...but it was not signed by us


def test_chain_links_reports_in_order():
    sealer = Sealer(KEY)
    first = _hero(sealer)
    second = sealer.seal(_hero(), prev_hash=first.seal.chain_hash)
    assert sealer.verify(second, expected_prev=first.seal.chain_hash).ok
    out_of_order = sealer.verify(second, expected_prev=GENESIS)
    assert not out_of_order.ok and "not linked" in out_of_order.reason
    broken = second.model_copy(
        update={"seal": second.seal.model_copy(update={"prev_hash": GENESIS})}
    )
    assert not sealer.verify(broken).ok
    assert not sealer.verify(second.model_copy(update={"seal": None})).ok


def test_markdown_report_tells_the_whole_story():
    md = render_markdown(_hero(Sealer(KEY)))
    for expected in (
        "**Outcome: DISCREPANCIES**",
        "RECEIPT DOES NOT MATCH",
        "NO RECEIPT",
        "RECEIPT FOUND",
        "| Field | Agent claimed | System of record | Match |",
        "**NO**",
        "## Audit trail",
        "## Seal",
        "signed (key",
    ):
        assert expected in md, expected


def test_markdown_for_clean_spoken_and_finding_reports():
    clean = render_markdown(run_scenario(BY_ID["clean-inquiry-only"]).report)
    assert "made no claims" in clean and "Every write the agent made is accounted for" in clean
    spoken = render_markdown(run_scenario(BY_ID["fault-spoken-only"]).report)
    assert "said to caller, never logged" in spoken
    finding = render_markdown(run_scenario(BY_ID["fault-double-book"]).report)
    assert "POLICY_VIOLATION: `double_booking`" in finding
    outage = render_markdown(run_scenario(BY_ID["edge-calendar-outage"]).report)
    assert "COULD NOT CHECK" in outage and "trust score: 100%" in outage


def test_naive_baseline_is_fooled_exactly_where_expected():
    def naive(scenario_id: str) -> list[VerdictStatus]:
        report = run_scenario(BY_ID[scenario_id], auditor="naive").report
        assert report.auditor.startswith("naive") and report.findings == ()
        return [v.status for v in report.verdicts]

    V, NF, UV = VerdictStatus.VERIFIED, VerdictStatus.NOT_FOUND, VerdictStatus.UNVERIFIABLE
    assert naive("fault-phantom-booking") == [NF, V]  # the easy case: it does catch this
    assert naive("edge-preexisting-match") == [V]  # fooled: something matching exists
    assert naive("fault-zombie-reschedule") == [V]  # fooled: new slot exists, old one too
    assert naive("fault-stale-confirmation") == [V, V]  # fooled: a message exists
    assert naive("fault-spoken-only") == []  # blind: nothing was logged
    assert naive("clean-cancel") == [V] and naive("clean-handoff") == [V]
    assert naive("edge-calendar-outage") == [UV, V]
    assert NaiveAuditor.NAME.startswith("naive") and CLINIC.name
