# Run execution mechanics

This package implements narrow mechanics owned by `app.modules.run`, not another Runtime owner. It must not import product owners, own ORM records, choose authorization, construct application resources, or assemble Context.

`scheduler.py` owns only ephemeral ready positions. Queue operations are synchronous, bounded and free of I/O. Runner owns admission, in-flight operations, lifecycle transitions and the decision to re-enqueue after one quantum. Taking a ready position does not start a Run or change its durable status. No queue state is restored as a checkpoint after process loss.

Tests for this primitive do not establish integrated Runner fairness, E2E behavior or the 50-Agent load target. Those remain the G005 real-entry acceptance surfaces.
