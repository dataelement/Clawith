# Run execution mechanics

This package implements narrow mechanics owned by `app.modules.run`, not another Runtime owner. It must not import product owners, own ORM records, choose authorization, construct application resources, or assemble Context.

`scheduler.py` owns only ephemeral ready positions. Queue operations are synchronous, bounded and free of I/O. Runner owns admission, in-flight operations, lifecycle transitions and the decision to re-enqueue after one quantum. Taking a ready position does not start a Run or change its durable status. No queue state is restored as a checkpoint after process loss.

`dispatcher.py` owns bounded process-local reservations, active quantum tasks and fair queue re-entry. Waiting retains admission while releasing execution capacity. The Run owner releases reservations only after creation rollback or terminal commit. Dispatcher shutdown drains actual operations before the owner commits service-wide interruption; cancellation cannot abandon its cleanup task.

Primitive tests do not establish integrated Runtime performance. The G005 application fixture and hostile Runtime scheduler tests exercise the assembled path; formal load qualification remains a separate environment-dependent result.
