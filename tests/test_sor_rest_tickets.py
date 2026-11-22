"""Exercise the ticket HTTP contract and handoff auditor in process."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from io import BytesIO
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pytest

from subpoena.auditor.engine import Auditor
from subpoena.models import CallRecord, Entity, HandoffCreated, PatientRef, VerdictStatus
from subpoena.sor.base import AdapterUnavailable
from subpoena.sor.rest_tickets import RestTickets
from subpoena.sor.sqlite import SqliteStore

NOW = datetime(2026, 1, 2, 12, tzinfo=UTC)
ROOT = "https://tickets.invalid/api/v2"
PATIENT_FIELD = 101
ACTOR_FIELD = 102


class FakeTicketService:
    """In-memory Zendesk-shaped API with a controllable clock and search lag."""

    def __init__(self):
        self.now = NOW
        self.tickets = {}
        self.keys = {}
        self.hide_search_results = False
        self.fail_page_two = False
        self.fail_response_after_create = False

    def open(self, request, *, timeout):
        parts = urlsplit(request.full_url)
        assert parts.netloc == "tickets.invalid"
        path = parts.path.removeprefix("/api/v2/")
        params = parse_qs(parts.query)
        if path == "tickets.json" and request.get_method() == "POST":
            key = request.get_header("Idempotency-key")
            if key in self.keys:
                return self.reply(ticket=self.keys[key])
            payload = json.loads(request.data)["ticket"]
            assert payload.pop("comment") == {"body": payload["subject"]}
            ticket_id = len(self.tickets) + 1
            ticket = {
                **payload,
                "id": ticket_id,
                "status": "new",
                "created_at": self.now.isoformat(),
            }
            self.tickets[ticket_id] = ticket
            if key:
                self.keys[key] = ticket
            if self.fail_response_after_create:
                self.fail_response_after_create = False
                raise TimeoutError
            return self.reply(ticket=ticket)
        if path == "tickets.json":
            index = int(params.get("page[after]", ["0"])[0])
            if index and self.fail_page_two:
                raise HTTPError(request.full_url, 503, "unavailable", {}, None)
            rows = list(self.tickets.values())
            more = index + 1 < len(rows)
            return self.reply(
                tickets=rows[index : index + 1],
                meta={"has_more": more},
                links={"next": f"?page[after]={index + 1}" if more else None},
            )
        if path == "search.json":
            query = params["query"][0]
            assert query.startswith("type:ticket external_id:")
            external_id = json.loads(query.removeprefix("type:ticket external_id:"))
            return self.reply(
                results=[
                    ticket
                    for ticket in self.tickets.values()
                    if ticket.get("external_id") == external_id and not self.hide_search_results
                ],
                next_page=None,
            )
        if path.startswith("tickets/") and path.endswith(".json"):
            ticket_id = int(path.removeprefix("tickets/").removesuffix(".json"))
            if ticket_id in self.tickets:
                return self.reply(ticket=self.tickets[ticket_id])
            raise HTTPError(request.full_url, 404, "not found", {}, None)
        raise AssertionError(f"unexpected request {request.full_url}")

    @staticmethod
    def reply(**body):
        return BytesIO(json.dumps(body).encode())


@pytest.fixture
def service():
    return FakeTicketService()


def adapter(service, *, actor="agent:call-1", complete=False):
    return RestTickets(
        ROOT,
        patient_field_id=PATIENT_FIELD,
        actor_field_id=ACTOR_FIELD,
        actor=actor,
        open_request=service.open,
        complete_creation_history=complete,
    )


def test_create_read_and_search(service):
    writer = adapter(service)
    external_id = 'handoff:call-1 "callback"'
    ticket = writer.create_ticket(
        patient_id="pat_1", summary="Call back", priority="urgent", external_id=external_id
    )

    reader = adapter(service, actor="unrelated")
    assert reader.get_ticket(ticket.id) == ticket
    assert reader.get_ticket("999") is None
    assert reader.find_by_external_id("missing") is None
    assert reader.find_by_external_id(external_id) == ticket
    assert reader.list_tickets(patient_id="pat_1") == [ticket]
    assert reader.list_tickets(patient_id="pat_2") == []
    assert (
        writer.create_ticket(patient_id="pat_1", summary="Retry", external_id=external_id) == ticket
    )
    assert len(service.tickets) == 1


def test_structured_topic_round_trips_in_the_configured_field(service):
    writer = RestTickets(
        ROOT,
        patient_field_id=PATIENT_FIELD,
        actor_field_id=ACTOR_FIELD,
        topic_field_id=103,
        actor="agent:call-1",
        open_request=service.open,
    )
    ticket = writer.create_ticket(patient_id=None, summary="Call back", topic="refill")
    assert writer.get_ticket(ticket.id).topic == "refill"
    (change,) = writer.changes(NOW, NOW)
    assert change.after["topic"] == "refill"


def test_retry_after_committed_create_with_search_lag(service):
    service.hide_search_results = True
    service.fail_response_after_create = True
    fields = {"patient_id": None, "summary": "Call back", "external_id": "call-1"}
    with pytest.raises(AdapterUnavailable):
        adapter(service).create_ticket(**fields)
    ticket = adapter(service).create_ticket(**fields)
    assert ticket.id == "1"
    assert len(service.tickets) == 1


def test_window_includes_both_bounds_and_read_preserves_actor(service):
    writer = adapter(service)
    service.now = NOW - timedelta(seconds=1)
    writer.create_ticket(patient_id=None, summary="Before")
    service.now = end = NOW + timedelta(seconds=4)
    last = writer.create_ticket(patient_id=None, summary="At end")
    service.now = NOW
    first = writer.create_ticket(patient_id=None, summary="At start")
    service.now = end + timedelta(seconds=1)
    writer.create_ticket(patient_id=None, summary="After")

    reader = adapter(service, actor="unrelated")
    assert reader.list_tickets(window=(NOW, end)) == [first, last]
    changes = reader.changes(NOW, end)
    assert [row.entity_id for row in changes] == [first.id, last.id]
    assert all(
        row.entity == Entity.TICKET and row.op == "insert" and row.actor == "agent:call-1"
        for row in changes
    )
    assert changes[0].after == first.model_dump(mode="json", exclude={"topic"})


def test_failed_later_page_is_unavailable(service):
    writer = adapter(service)
    for _ in range(2):
        writer.create_ticket(patient_id=None, summary="Call back")
    service.fail_page_two = True
    with pytest.raises(AdapterUnavailable, match="503"):
        writer.changes(NOW, NOW)


@pytest.mark.parametrize(
    ("actor", "priority", "complete", "expected"),
    [
        ("agent:call-1", "urgent", True, VerdictStatus.VERIFIED),
        ("agent:call-1", "low", True, VerdictStatus.MISMATCH),
        ("agent:other-call", "urgent", True, VerdictStatus.NOT_FOUND),
        (None, "urgent", True, VerdictStatus.NOT_FOUND),
        ("agent:call-1", "urgent", False, VerdictStatus.UNVERIFIABLE),
        (None, "urgent", False, VerdictStatus.UNVERIFIABLE),
    ],
)
def test_handoff_verdict_uses_rest_ticket_evidence(service, actor, priority, complete, expected):
    store = SqliteStore()
    sor = store.session("seed", lambda: NOW - timedelta(days=1))
    caller = PatientRef(name="Pia Smith", phone="555-0101")
    patient = sor.crm.create_patient(name=caller.name, phone=caller.phone)
    if actor is not None:
        adapter(service, actor=actor).create_ticket(
            patient_id=patient.id, summary="Call back", priority=priority
        )
    sor = replace(sor, tickets=adapter(service, actor="unrelated", complete=complete))
    call = CallRecord(
        call_id="call-1",
        agent_id="agent",
        caller=caller,
        started_at=NOW,
        ended_at=NOW + timedelta(seconds=5),
        claims=(HandoffCreated(claim_id="c1", turn_index=0, patient=caller, priority="urgent"),),
    )

    report = Auditor(sor, use_transcript=False, clock=lambda: NOW).audit(call, audit_id="audit-1")

    assert [verdict.status for verdict in report.verdicts] == [expected]
