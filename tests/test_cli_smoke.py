"""Exercise the CLI entry point with local inputs and blocked sockets."""

import argparse
import socket
from pathlib import Path

import pytest

from subpoena.cli import build_parser, main
from subpoena.evals import certify

_TWILIO = Path(__file__).resolve().parent / "fixtures" / "twilio"
_CALLBACKS = [
    str(_TWILIO / "completed-status.json"),
    str(_TWILIO / "completed-transcription.json"),
]


def _subcommands():
    (subparsers,) = (
        action
        for action in build_parser()._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    return list(subparsers.choices)


@pytest.mark.parametrize("command", _subcommands())
def test_subcommand_help(command, capsys):
    # serve is help-only because starting the API requires a listening socket.
    with pytest.raises(SystemExit) as exc:
        main([command, "--help"])
    assert exc.value.code == 0
    assert f"usage: subpoena {command}" in capsys.readouterr().out


@pytest.fixture
def offline(monkeypatch):
    def deny_network(*args, **kwargs):
        pytest.fail("The CLI attempted to use the network")

    monkeypatch.setattr(socket, "socket", deny_network)
    monkeypatch.setattr(socket, "create_connection", deny_network)
    monkeypatch.setattr(socket, "getaddrinfo", deny_network)
    monkeypatch.delenv("SUBPOENA_SIGNING_KEY", raising=False)


def test_demo_and_verify_offline(offline, tmp_path, capsys):
    report = tmp_path / "report.json"
    assert main(["demo", "--fast", "--json", str(report)]) == 0
    assert report.is_file()
    capsys.readouterr()

    assert main(["verify", str(report)]) == 0
    assert capsys.readouterr().out.startswith("VALID ")


def test_eval_offline(offline, capsys):
    assert main(["eval", "--fuzz", "1", "--seed", "5"]) == 0
    assert capsys.readouterr().out.splitlines()[-1] == "PASS"


def test_certify_offline(offline, monkeypatch, tmp_path):
    monkeypatch.setattr(certify, "DEFAULT_DIR", tmp_path)
    assert main(["certify"]) == 0
    assert (tmp_path / "latest.json").is_file()


def test_validate_twilio_offline(offline):
    assert main(["validate-twilio", *_CALLBACKS]) == 0


def test_ingest_twilio_offline(offline, tmp_path):
    record = tmp_path / "record.json"
    assert (
        main(
            [
                "ingest-twilio",
                *_CALLBACKS,
                str(_TWILIO / "completed-call-resource.json"),
                "--agent-id",
                "test-agent",
                "--agent-version",
                "v1",
                "--caller-name",
                "Test Caller",
                "--out",
                str(record),
            ]
        )
        == 0
    )
    assert record.is_file()
