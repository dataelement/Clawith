# Agent Note: Keep input attachments under their product source

Status: implemented — Session and Group publication and authorization, raw HTTP uploads, application storage and explicit attachment Tools.

## Problem

An input reference alone does not retain a file or authorize reading it. Moving every upload into Workspace would conflate message data with editable files. Opening every file in a conversation to every concurrent Run would also bypass the fixed input cutoff.

## Decision

Session and Group each keep their own attachment records. The application stores immutable bytes under generated owner-scoped keys through typed storage ports; owners do not import concrete storage or each other's tables. Each upload has a stable source key, content digest, size and publication receipt. The first release accepts at most 4 MiB per attachment and expires unbound uploads after 24 hours. A later reference to a submitted file retains its original binding rather than rewriting provenance.

Metadata registration precedes physical publication. Only a matching storage revision, byte size and digest can publish the file. Binding requires a published file explicitly referenced by the accepted input and commits with that input. Session files remain private to their Membership; Group uploads remain uploader-private until submitted. Filenames are display metadata and never choose a storage path.

Execution authorization uses the initiating Main's fixed input cutoff plus explicitly recorded Run input references. Future conversation files are not automatically readable. Subagents inherit the Parent Main's readable sources, including references explicitly accepted by Parent later. This permits an explicit read when Child knows the reference; it does not inject the new input into Child Context, enumerate later conversation files or create a separate Child ACL. A2A targets require an injected verifier of the request's explicit file delegation; a textual reference, target Agent identity or default Workspace scope does not authorize sender files.

Trigger and Heartbeat may read an explicitly referenced attachment only when their captured Workspace output subject matches that attachment's original Membership or Group. An Agent-owned default scope never gains private attachment access from a reference string. Scheduled execution does not receive another Session's unrestricted history or bypass the source subject check.

The application serializes upload/publication and cleanup with the same per-object storage guard. It rechecks the owner record after acquiring that guard and performs bounded I/O without a business transaction. Cleanup first locks and claims an expired, unbound record in a short transaction, recording `cleanup_claimed_at`; binding, publication and reading reject that committed claim. This prevents a binding transaction from committing after the physical deletion decision, even when its caller supplied a pre-expiry timestamp. The application commits the claim before conditional physical deletion, then removes metadata only for the matching claim, publication, revision, key and digest. A crash leaves a claimed record eligible for bounded cleanup, not an Agent execution to replay. Bound input files are not removed by temporary-upload cleanup.

## Alternatives considered

Automatic Workspace import was rejected because input files need not become editable Workspace files. A generic Artifact owner would add a new product authority where Session and Group already own the inputs. Automatically granting all later conversation attachments would violate the fixed-cutoff decision. A boolean delegation bypass was rejected in favor of an explicit owning verifier injected by application composition.

## Consequences

Upload acceptance, byte publication and input binding remain distinct facts. Failed publication leaves a discoverable unbound record. Filenames must encode as UTF-8 and MIME metadata uses an ASCII type/subtype; transports normalize header parameters before owner intake. Channel resource download requires its authenticated provider integration. Images use explicitly bounded previews; a reference or base64 string alone is not proof that a model inspected an image. Binary storage does not imply PDF or Office text extraction.

Application upload orchestration admits at most four upload bodies, each bounded to four MiB. A separate four-slot gate limits physical reads, writes and cleanup; an unfinished client upload does not occupy storage-read capacity. Publication and cleanup acquire their object guard before the storage gate, avoiding a lock-order inversion. The application rechecks the owner record under that guard and publishes only verified storage metadata. Cancellation releases body admission and drains a started write before releasing its storage guard. The Tool reader verifies stored revision, size and SHA-256 before returning bytes. Explicit Workspace saving uses the Run's existing output scope and ordinary `files/` path; upload alone never imports a file into Workspace. Periodic cleanup commits claims before conditional physical deletion and leaves failed work discoverable.

`send_message` accepts authorized attachment references and explicit Workspace files identified by `files/` path, expected revision and output-or-Agent subject. The application captures original bytes into the destination's immutable attachment storage before accepting the message. A changed Workspace revision rejects capture; later Workspace edits do not change the accepted file. Messages accept at most eight files, four MiB per file and sixteen MiB total. Existing input references are copied into the destination owner as well, so delivery does not depend on a later change to sender authorization or a mutable path.

Run-created uploads record their real `created_by_run_id` and have no human uploader. Human uploads retain the real uploader and no Run creator; these identities are mutually exclusive. New Run publication verifies the Main's actual captured `send_message` call. Direct sources must match their own conversation; unattended sources require the injected frozen-destination/private-origin verifier inside the attachment owner. The application rechecks the prepared upload under its publication guard and at publication, without manufacturing a Principal.

Run-created files bind to their accepted reply through `bound_message_id` in the message-acceptance transaction. They never borrow an unrelated human input for provenance or retention. Cleanup requires both input binding and message binding to be absent, so delivered files are retained. Humans cannot claim or read a Run's unaccepted upload. Channel delivery requires an accepted message explicitly naming the file and verifies that the sending Run can read that source; an arbitrary reference string is not a new grant. Failed or cancelled capture remains an unbound upload eligible for the same claim-based cleanup.

After message binding, a generated attachment is readable by its originating Main and inherited Children; other Runs need a fixed cutoff covering the accepted reply or an explicit Run input reference. Reusing that file in a later human input preserves its message provenance. Human upload source keys cannot use the reserved `message:` namespace.

An already accepted Run/step/call returns its existing owner message receipt before reading sources or checking whether the Run is still executing. This preserves idempotence after the source Workspace changes or the Run finishes; replay never recaptures files or adds another message.

## Verification

Message attachment owner tests verify actual Main Tool correlation, refusal of invented calls, binding only to an accepted message, retention after binding, concurrent Run cutoff isolation and refusal to publish a cleanup-claimed orphan. `tests/e2e/test_message_files.py` checks Session and Group output, Agent Workspace sources, stale revision rejection, immutable delivery after source modification, accepted-message replay, count and total-byte limits, and real cleanup of partially captured files without a reply. Native Channel upload and send require their separate provider-path tests.

Owner tests use real PostgreSQL for immutable publication receipts, input-binding rollback, Membership/Group isolation, fixed cutoffs, explicit related references, Subagent inheritance, delegation-port denial, upload bounds, binding/cleanup races and conditional claimed metadata cleanup. Physical storage deletion, actual file bytes, A2A grant production, image processing and assembled Model requests require separate application-path tests; owner rows alone do not prove those outcomes.

`tests/e2e/test_attachments.py` exercises the ASGI application with real PostgreSQL and file storage and a controlled Model transport. It verifies raw upload bounds, private downloads, conditional physical cleanup, cancellation during publication, explicit text and reduced-image reads in subsequent Model requests, A2A's exact file subset and `save_attachment` preserving original binary bytes in the captured Membership Workspace. These tests do not prove live Channel provider downloads or live-model interpretation.

`tests/e2e/test_attachment_upload_concurrency.py` keeps four real authenticated request bodies unfinished while another attachment download completes. Cancelling those uploads closes their body generators and permits a subsequent upload. This verifies upload/read capacity isolation, not overall 50-Agent load qualification.
