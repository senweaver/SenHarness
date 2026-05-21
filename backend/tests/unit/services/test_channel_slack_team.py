"""M0.8 — slack ``expected_team_id`` pinning."""

from __future__ import annotations

import pytest

from app.services.channels.base import SignatureInvalid
from app.services.channels.slack import SlackProvider


def test_team_id_skipped_when_unset() -> None:
    SlackProvider().assert_team_id(channel_config={}, payload={"team_id": "T123"})


def test_team_id_passes_on_match() -> None:
    SlackProvider().assert_team_id(
        channel_config={"expected_team_id": "T123"},
        payload={"team_id": "T123"},
    )


def test_team_id_raises_on_mismatch() -> None:
    with pytest.raises(SignatureInvalid) as exc:
        SlackProvider().assert_team_id(
            channel_config={"expected_team_id": "T123"},
            payload={"team_id": "T999"},
        )
    assert exc.value.code == "slack.team_id_mismatch"


def test_team_id_raises_when_payload_missing_team() -> None:
    with pytest.raises(SignatureInvalid) as exc:
        SlackProvider().assert_team_id(
            channel_config={"expected_team_id": "T123"},
            payload={},
        )
    assert exc.value.code == "slack.team_id_mismatch"
