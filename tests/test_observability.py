import json
import logging

from steward.observability import trace


def test_trace_emits_structured_local_event_without_message_content(caplog) -> None:
    with caplog.at_level(logging.INFO, logger="steward.trace"):
        trace("graph.retrieval", fragment_ids=[4, 9], result_count=2)

    assert json.loads(caplog.messages[0]) == {
        "event": "graph.retrieval", "fragment_ids": [4, 9], "result_count": 2
    }
