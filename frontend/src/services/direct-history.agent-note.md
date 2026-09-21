# Direct history page assembly

`loadDirectHistoryTurn` owns the bounded assembly of one visible direct-chat
history unit. Ordinary history consumes one page; a folded Tool turn may fetch
up to `maxContinuationPages` pages to reach its user-message boundary.

Pages arrive newest first, while each page is chronological. Keep each page until
the unit is complete, reverse the page order, and flatten once at publication.
For N rows this copies O(N) row references instead of repeatedly copying the
growing result on each request. The page-request limit, cursor validation,
previous-turn trimming, and all-or-error publication remain unchanged.

The pagination tests exercise short and empty pages, the maximum continuation
budget, cursor stalls, and exact chronological assembly without mutating pages.
Run `node --test tests/directHistoryPagination.test.mjs` from `frontend/`.
