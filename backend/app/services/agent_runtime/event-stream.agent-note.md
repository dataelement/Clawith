# Persisted Runtime event pagination

`DatabaseRuntimeEventStream` owns ordered, tenant/run-scoped reads of persisted
`AgentRunEvent` rows. `stream_web_chat_run` consumes those rows to publish the
existing Web Chat protocol; it requires a delivery event to publish `done`.

A full page does not prove that the stream reached the latest event, even when
it contains a terminal Run event and the Run's delivery status is settled.
The matching delivery event can already exist on the next page. Drain full
pages before applying the existing terminal/delivery closure condition.
Otherwise Web Chat can fail with `runtime_stream_ended_without_delivery`.

Keep each read bounded by `batch_size` and release its database session before
yielding events. Between full pages, yield to the event loop with `sleep(0)`
for fairness/cancellation but do not impose the idle polling interval on
persisted backlog. A short page uses the existing closure or idle-poll policy.
An exactly full final page therefore incurs one additional read. Cursor order,
authorization scope, delivery policy and event schemas are unchanged.

Verification: `tests/test_agent_runtime_event_stream.py` covers successful,
failed and cancelled Runs, successful/failed delivery on the next page,
exactly full final pages, backlog scheduling, and Web Chat `done` through the
real adapter. Existing chat-stream and delivery tests cover the consumers.
This is a pagination guarantee, not a new transaction-isolation or reconnect
policy; it does not claim to fix all causes of interrupted live streams.
