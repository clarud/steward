from datetime import datetime

import pytest

from steward.events import IncomingEvent


def test_incoming_event_requires_timezone_aware_timestamp() -> None:
    with pytest.raises(ValueError, match="timezone"):
        IncomingEvent(
            id="telegram:1",
            platform="telegram",
            chat_id="1",
            message_id="1",
            reply_to_id=None,
            timestamp=datetime(2026, 9, 7),
            text="Hello",
        )
