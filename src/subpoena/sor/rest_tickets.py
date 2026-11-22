"""Zendesk-shaped ticket queue and creation evidence for the handoff auditor."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from datetime import datetime
from hashlib import sha256
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from subpoena.models import Change, Entity, HandoffTopic, Priority, Ticket
from subpoena.sor.base import COMPLETE, AdapterUnavailable, Coverage, TicketQueueAdapter


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class RestTickets(TicketQueueAdapter):
    """Use an API root such as https://example.zendesk.com/api/v2.

    Configure text custom fields for the CRM patient ID and creation actor. The
    topic_field_id optionally maps the structured topic to a custom field. The
    writer's actor should be scoped to the call (e.g. "agent:call-id"). Reads
    attribute evidence only to the actor persisted on each remote record.

    Enable complete_creation_history only for endpoints that expose every
    creation in the window with immutable creation data and no unobserved
    mutations. A normal Zendesk ticket listing is a current-state snapshot, so
    coverage defaults to incomplete.

    For retries, use a stable, unique external_id per intended ticket. Search
    recovers existing tickets and Idempotency-Key protects concurrent creates
    and retries while the server retains the key. This depends on backend
    support for that header and search visibility after the key expires.
    """

    def __init__(
        self,
        api_root: str,
        *,
        patient_field_id: int,
        actor_field_id: int,
        actor: str = "",
        authorization: str | None = None,
        complete_creation_history: bool = False,
        timeout: float = 10,
        open_request: Callable[..., Any] | None = None,
        topic_field_id: int | None = None,
    ) -> None:
        self._root = api_root.rstrip("/") + "/"
        root = urlsplit(self._root)
        if root.scheme not in {"http", "https"} or not root.hostname:
            raise ValueError("ticket API root must be an HTTP(S) URL")
        self._origin = (root.scheme, root.netloc)
        self._patient_field = patient_field_id
        self._actor_field = actor_field_id
        self._topic_field = topic_field_id
        self._actor = actor
        self._authorization = authorization
        self._complete = complete_creation_history
        self._timeout = timeout
        self._open = open_request or build_opener(_NoRedirects()).open

    def _request(
        self,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        allow_missing: bool = False,
    ) -> dict[str, Any] | None:
        url = urljoin(self._root, path)
        parts = urlsplit(url)
        if (parts.scheme, parts.netloc) != self._origin:
            raise AdapterUnavailable("ticket API returned a pagination URL on another origin")
        headers = {"Accept": "application/json"}
        if self._authorization is not None:
            headers["Authorization"] = self._authorization
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload).encode()
        try:
            with self._open(Request(url, data=data, headers=headers), timeout=self._timeout) as res:
                body = json.load(res)
        except HTTPError as exc:
            exc.close()
            if allow_missing and exc.code == 404:
                return None
            raise AdapterUnavailable(f"ticket API returned HTTP {exc.code}") from exc
        except (OSError, ValueError) as exc:
            raise AdapterUnavailable("ticket API request failed or returned invalid JSON") from exc
        if not isinstance(body, dict):
            raise AdapterUnavailable("ticket API returned an invalid response")
        return body

    def _records(self, path: str, key: str) -> Iterator[dict[str, Any]]:
        seen = set()
        while path:
            url = urljoin(self._root, path)
            if url in seen:
                raise AdapterUnavailable("ticket API repeated a pagination URL")
            seen.add(url)
            page = self._request(url)
            assert page is not None
            records = page.get(key)
            if not isinstance(records, list) or any(not isinstance(r, dict) for r in records):
                raise AdapterUnavailable("ticket API returned an invalid ticket list")
            yield from records
            try:
                next_page = page.get("next_page") or page.get("links", {}).get("next")
                if next_page is not None and not isinstance(next_page, str):
                    raise TypeError
                if page.get("meta", {}).get("has_more") and not next_page:
                    raise ValueError
            except (AttributeError, TypeError, ValueError) as exc:
                raise AdapterUnavailable("ticket API returned invalid pagination") from exc
            path = urljoin(url, next_page) if next_page else ""

    def _decode(self, record: dict[str, Any]) -> tuple[Ticket, str]:
        try:
            fields = {field["id"]: field["value"] for field in record.get("custom_fields", [])}
            status = record["status"]
            if status not in {"new", "open", "pending", "hold", "solved", "closed"}:
                raise ValueError("unknown ticket status")
            if not isinstance(record["id"], (str, int)) or not record["id"]:
                raise ValueError("missing ticket ID")
            priority = record.get("priority")
            ticket = Ticket(
                id=str(record["id"]),
                patient_id=fields.get(self._patient_field),
                summary=record["subject"],
                priority="urgent" if priority == "high" else priority or "normal",
                status="closed" if status in {"solved", "closed"} else "open",
                created_at=record["created_at"],
                topic=fields.get(self._topic_field),
            )
            actor = fields.get(self._actor_field)
            return ticket, actor if isinstance(actor, str) else ""
        except (KeyError, TypeError, ValueError) as exc:
            raise AdapterUnavailable("ticket API returned an invalid ticket") from exc

    def _one(self, body: dict[str, Any]) -> Ticket:
        record = body.get("ticket")
        if not isinstance(record, dict):
            raise AdapterUnavailable("ticket API response is missing its ticket")
        return self._decode(record)[0]

    def get_ticket(self, ticket_id: str) -> Ticket | None:
        body = self._request(f"tickets/{quote(ticket_id, safe='')}.json", allow_missing=True)
        return self._one(body) if body is not None else None

    def find_by_external_id(self, external_id: str) -> Ticket | None:
        query = urlencode({"query": f"type:ticket external_id:{json.dumps(external_id)}"})
        matches = {
            ticket.id: ticket
            for record in self._records(f"search.json?{query}", "results")
            if record.get("external_id") == external_id
            for ticket, _ in [self._decode(record)]
        }
        if len(matches) > 1:
            raise AdapterUnavailable("multiple tickets share the requested external ID")
        return next(iter(matches.values()), None)

    def create_ticket(
        self,
        *,
        patient_id: str | None,
        summary: str,
        priority: Priority = "normal",
        external_id: str | None = None,
        topic: HandoffTopic | None = None,
    ) -> Ticket:
        ticket = {
            "subject": summary,
            "comment": {"body": summary},
            "priority": priority,
            "custom_fields": [
                {"id": self._patient_field, "value": patient_id},
                {"id": self._actor_field, "value": self._actor},
            ],
        }
        if topic is not None:
            if self._topic_field is None:
                raise ValueError("configure a ticket topic field before writing a topic")
            ticket["custom_fields"].append({"id": self._topic_field, "value": topic})
        key = None
        if external_id is not None:
            if not external_id:
                raise ValueError("external_id must be nonempty")
            existing = self.find_by_external_id(external_id)
            if existing is not None:
                return existing
            ticket["external_id"] = external_id
            key = sha256(external_id.encode()).hexdigest()
        body = self._request("tickets.json", payload={"ticket": ticket}, idempotency_key=key)
        assert body is not None
        return self._one(body)

    def _listed(self, window: tuple[datetime, datetime] | None) -> list[tuple[Ticket, str]]:
        rows = {}
        for record in self._records("tickets.json?page%5Bsize%5D=100", "tickets"):
            ticket, actor = self._decode(record)
            if window is None or window[0] <= ticket.created_at <= window[1]:
                row = (ticket, actor)
                if ticket.id in rows and rows[ticket.id] != row:
                    raise AdapterUnavailable("ticket changed during pagination")
                rows[ticket.id] = row
        return sorted(rows.values(), key=lambda row: (row[0].created_at, row[0].id))

    def list_tickets(
        self,
        *,
        patient_id: str | None = None,
        window: tuple[datetime, datetime] | None = None,
    ) -> list[Ticket]:
        return [
            ticket
            for ticket, _ in self._listed(window)
            if patient_id is None or ticket.patient_id == patient_id
        ]

    def changes(self, since: datetime, until: datetime) -> list[Change]:
        return [
            Change(
                seq=seq,
                entity=Entity.TICKET,
                entity_id=ticket.id,
                op="insert",
                before=None,
                after=ticket.model_dump(
                    mode="json", exclude={"topic"} if ticket.topic is None else None
                ),
                actor=actor,
                at=ticket.created_at,
            )
            for seq, (ticket, actor) in enumerate(self._listed((since, until)), start=1)
        ]

    def coverage(self, since: datetime, until: datetime) -> Coverage:
        if self._complete:
            return COMPLETE
        return Coverage(False, "ticket snapshots can omit mutations and deleted tickets")
