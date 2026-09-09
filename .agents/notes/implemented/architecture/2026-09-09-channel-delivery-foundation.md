# Agent Note: Source-backed Channel delivery and explicit uncertain attempts

Status: implemented — configuration, mapping, source-backed delivery, seven native adapters and application-owned transport consumers. Hosted-provider qualification remains unverified.

## Problem

A provider response may be lost after an external message is sent. Retrying every failed call can duplicate delivery, while copying message text into a delivery record creates a competing authority beside Session or Group. Generic transport interfaces alone do not preserve the legacy provider capabilities.

## Decision

Channel stores only the accepted source-message relation, destination and attempt outcomes. Its injected message loader uses Session/Group public services. Each send marks an uncertain attempt and commits before external I/O; subsequent callers cannot automatically resend that attempt. Confirmed provider success records acknowledgement; definite rejection records failure; transport loss, cancellation and ambiguous provider failure remain uncertain. No Run or Tool is replayed.

Configuration and external identity mappings remain Channel-owned. Tenant or same-Agent Credentials are validated through Credential's public API; personal credentials cannot be bound as a shared Channel account. Slack requests require a valid HMAC signature and a five-minute timestamp window, then the configured workspace identity and explicit actor mapping. Bot events do not become human input. External HTTP clients are stateless and application-owned.

Channel-owned conversation mappings reuse one Session for the configured external conversation and Membership. Authenticated actors resolve through Identity and Permission public services; intake never invents a Principal or provisions an account. Product input and its Channel route commit together before Run admission. A reply to an acknowledged Waiting question retains its explicit Run/reference; ordinary messages create another Main within the same conversation Session.

Message acceptance remains independent of Channel availability. A durable Channel message cursor consumes committed Session/Group positions and enqueues delivery in the same Channel transaction as cursor advancement. Post-commit notifications only accelerate this consumer; losing a notification cannot lose the message or replay the Run. Batched owner-provided heads avoid querying every idle conversation page. The cursor advances across human input and other-Agent replies as well as deliverable messages. Delivery preserves enqueue order within one Channel destination while independent destinations can proceed concurrently.

Explicitly authorized scheduled replies retain their real Trigger or Heartbeat Run and have no fabricated human input origin. Channel routes those accepted replies through the destination already held by its conversation or Group mapping. Group mappings cover the default conversation only; other-topic replies remain product-visible and do not enter that external conversation. A provider requiring unavailable reply context records an independent failed delivery. Channel neither borrows another event's context nor blocks later source positions because the message lacks a human input origin.

Provider reply tokens and authenticated transport coordinates are Channel facts, not product message content or reusable Credential accounts. Channel encrypts a closed, versioned context with a domain-separated HKDF key and AES-GCM, binding its Tenant, Agent, configuration, event, identity and expiry as authenticated data. A product consumer receives only the opaque context ID. Each read rejects expired or unauthenticated data. Bounded cleanup clears expired ciphertext and nonce but retains message associations and metadata. Discord slash responses edit the deferred original response using this private context; ordinary Bot messages use the distinct channel-message endpoint.

Retransmission of an already routed event retains its original context identity without renewing its expiry or decrypting expired coordinates. It acknowledges transport receipt without resubmitting the old product input. Restarting a Channel must not restart pending inputs or interrupted Runs; delivery independently passes the normal context-expiry check.

The application owns enabled Channel listeners, delivery consumption and reply-context cleanup. Shutdown cancels these workers before closing Runtime or transport resources; it does not drain every delivery. Configuration or Credential changes replace the corresponding listener. Protocol authentication failures remain observable and do not provision users. Running listener tasks alone are not a connection-readiness signal.

Native listener public boundaries normalize socket closure, transport timeout and transport-only task-group failures as reconnectable disconnections. Authentication/configuration failures remain stopped; mixed task-group defects and cancellation are not converted into reconnect signals. Reconnection reuses existing source-event deduplication and never recreates accepted Runs.

WeChat QR requests are administrator-owned, bounded and expire after five minutes. Confirmation publishes or rotates an Agent-owned Credential before starting its listener; QR secrets and polling tokens never enter product input. WeCom customer-service configurations pin one `open_kfid` and do not require an enterprise-application `agent_id`. Authenticated notices own encrypted pagination coordinates under a separate cryptographic domain. Each notice retains its own cursor so overlapping notices cannot overwrite unfinished pages. Transport pagination may resume after restart, but duplicate source events cannot start old Runs. External polling and downloads never hold database locks.

For multiple product messages replying to the same Discord interaction, the first delivery owns the original-response edit and subsequent deliveries own distinct follow-up messages. Channel selects and persists this operation under its configuration lock, with one original delivery per context enforced by PostgreSQL. An uncertain follow-up POST is not retried automatically. Later messages cannot overwrite earlier source-owned messages by editing the same original response.

Provider protocols sometimes require secrets in URL paths or query strings. Those requests use a private URL representation that redacts string and diagnostic output while preserving the actual wire coordinates. It does not alter global logging, HTTP client ownership, connection pooling or cookie isolation.

## Alternatives considered

Keeping text beside the delivery source would duplicate the message authority. Holding a transaction across a provider call would consume database capacity during external waits. Automatically retrying uncertain delivery would risk duplicate external effects. These approaches are not used.

## Consequences

Slack, Discord and Teams split bounded source text into ordered provider-sized fragments within one delivery attempt. Full success requires every fragment to be acknowledged. Rejection before any successful fragment may be retried; a failure or cancellation after a successful fragment leaves the delivery uncertain and cannot replay the whole message. The record retains bounded first/last acknowledgement IDs, or the last ID when both do not fit. There is no per-fragment recovery guarantee or separate fragment state machine.

Every confirmed provider message ID is separately retained in the delivery's bounded `provider_reply_ids` array: at most 256 distinct nonempty UTF-8 strings of at most 512 bytes each. This metadata, not the display acknowledgement, resolves quoted replies against the exact Tenant, Channel and destination. Quoting a middle fragment of a Waiting question can therefore resume the same Run. A partial delivery can retain its confirmed IDs while remaining uncertain and non-retryable. Upload handles and aggregate acknowledgement descriptions are not message IDs. Discord Gateway captures the authenticated `message_reference.message_id` and rejects cross-conversation quote coordinates.

Authenticated native media is downloaded under the configured Channel Credential after product-scope authorization, then materialized as Session or Group attachments without an automatic Workspace copy. Outbound Slack and Feishu files load only immutable attachments explicitly referenced by the committed source message. File reading, upload and delivery occur outside database transactions, after the delivery reservation commits. Text and file parts share one delivery outcome; any acknowledged part prevents whole-message replay after a later failure. This does not establish live provider qualification.

The retained media boundary below comes from reachable producers and consumers at `8ed4ae2f`, not from Provider API possibilities or unused helpers. “File” includes sending image bytes as an ordinary file; it does not claim a native image-message type. Current adapter methods establish code availability, while the authorized product media bridge and its end-to-end tests establish whether product messages can use them. Neither establishes live Provider qualification.

| Provider | Legacy inbound text / image / file | Legacy outbound text / image / file | Retained connection | Current implementation owner |
| --- | --- | --- | --- | --- |
| Slack | Yes / file attachment / yes | Yes / ordinary file / yes | Signed Events HTTP | `channel/adapters.py`: receive, download_file, send, send_file |
| Feishu | Yes, including rich text / yes / yes | Yes / ordinary file / yes | Authenticated webhook and native WebSocket | `channel/providers/feishu.py`: receive, listen, download_resource, upload_file, send_file, send |
| DingTalk | Yes / yes / yes | Yes / no reachable delivery caller / no reachable delivery caller | Native Stream callbacks | `channel/providers/dingtalk.py`: listen, download_media, send; upload_file/send_file also exist as adapter primitives |
| Discord | Yes / no / no | Yes / no / no | Signed interactions and Gateway DM/mention | `channel/providers/discord.py` and `discord_gateway.py`: receive, listen, send; media primitives are not evidence of a legacy requirement |
| Teams | Yes / no / no | Yes / no / no | Bot Framework authenticated HTTP | `channel/providers/teams.py`: receive and fragmented send; no file-consent feature |
| WeChat iLink | Yes / no / no | Yes / no / no | QR login/status/image, long polling, session expiry | `channel/providers/wechat.py`: QR methods, poll_once, listen, fragmented send; QR image is not chat image support |
| WeCom | Yes / explicitly unsupported / explicitly unsupported | Yes / no / no | Encrypted webhook, AI-bot WebSocket and customer-service polling | `channel/providers/wecom.py`: receive, listen, customer-service intake/send; media primitives exceed the legacy text-only path |

Legacy source evidence:

- Slack `backend/app/api/slack.py:302` downloads event files. `backend/app/services/agent_tools.py:7067` has a reachable human-target file sender using `files.getUploadURLExternal` and `files.completeUploadExternal`; text delivery alone does not preserve that capability.
- Feishu `backend/app/api/feishu.py:570` handles rich-text images and `:641` accepts image/file messages. `backend/app/services/agent_tools.py:7027` and `:7172` call `feishu_service.upload_and_send_file`; `backend/app/services/feishu_service.py:587` uploads and sends a native `file` message.
- DingTalk `backend/app/services/dingtalk_stream.py:103` handles picture input and the same parser handles files. Its `_send_dingtalk_media_message` at `:289` supports native image/file forms but has no caller in the retained Backend source; an unused helper is not an end-to-end delivery capability.
- Discord `backend/app/services/discord_gateway.py:102` consumes message text; `backend/app/api/discord_bot.py:151` registers a string-valued `/ask`. Teams `backend/app/api/teams.py:472` extracts text and skips an empty text body. Their legacy delivery paths send text rather than binary attachments.
- WeChat `backend/app/services/wechat_channel.py:190` extracts text and returns when none exists; `send_wechat_text_message` at `:74` sends text chunks using the private context token. WeCom `backend/app/services/wecom_stream.py:198` and `:213` explicitly report image/file processing as unsupported; `backend/app/api/wecom.py:446` leaves those webhook variants unimplemented, and its customer-service consumer at `:498` accepts text.

Atlassian Rovo belongs to the separate Tool/Market handoff. WhatsApp is excluded by the [capability disposition matrix](../../../../backend/rewrite/backend-capability-coverage-matrix.md): its old route was unmounted, so its source does not establish an eighth retained message Provider.

## Verification

`tests/e2e/test_scheduled_channel_delivery.py` verifies real scheduled Model/Tool messages entering existing Session and Group destinations and reaching Slack. The same accepted replies produce independent Teams delivery failures when authenticated reply context is absent. Subsequent messages advance both Channel cursors, no human input is fabricated, and non-default Group conversation output is not forwarded to the default external mapping.

Application tests cover WeChat QR publication into real encrypted Credentials, concurrent confirmation, authenticated polling and listener cancellation; WeCom customer-service signed notices, encrypted two-page synchronization and restart without duplicate Runs; Slack two-file input, explicit model Tool reads and native outbound publication, including rejection after an earlier part succeeded; Feishu native download, immutable product attachment and upload/send; and DingTalk native download into the product attachment owner. The focused application suite passed eight tests; the Channel owner suite passed 104 tests. External HTTP is controlled: hosted accounts, real Provider connectivity and the 50-Agent platform load target remain unqualified.

Controlled HTTP tests exercise actual Slack parsing and sending, signature rejection, replay-window checks, cross-workspace rejection, bot filtering and uncertain sends without automatic retry. PostgreSQL tests use real Session/Run accepted Waiting messages as delivery sources, verify Credential boundaries and Tenant isolation, and prove concurrent/cancelled sends retain one attempt. Slack product HTTP E2E uses actual application resources and Model/Tool execution with only external HTTP controlled. It covers configuration, actor mapping, stable Session reuse, explicit Waiting reply, Final without another reply and cursor recovery after message commit without Run replay. No external workspace, seven-provider completion or load qualification is established by this evidence.

Discord integration tests persist a real authenticated slash context in PostgreSQL, preserve the source-owned Session message, and send exactly one HTTP PATCH to the original interaction response. Codec tests cover key rotation, owner/event/expiry substitution, closed input fields and redaction. HTTPX logging tests verify secret URL coordinates remain absent from INFO logs and public representations while the transport receives their real bytes.

Teams Managed Identity uses an explicitly granted deployment identity and the Azure SDK's `ManagedIdentityCredential`, not a developer-credential fallback chain. Each send owns and closes the credential transport and reuses its token only within that send's fragments. Real SDK tests against a controlled metadata HTTP server verify success, rejection and cancellation all close the actual asynchronous transport. No real Azure identity or tenant qualification is claimed.
