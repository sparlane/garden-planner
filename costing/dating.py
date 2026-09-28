"""Decide what date each layer one recalculation writes is effective from.

The subledger dates a layer twice: `created` is the audit stamp of the run
that wrote it, and `effective_at` is the day the fact behind it happened.
`costing.services` posts the layers; this is the one place that decides which
day each of them gets, and `costing.test_effective_dates` is where the rules
below are pinned.

It is its own module because `costing.services` is at its line ceiling, the
same reason `sales.cost_of_sale` is separate from `sales.commerce`.
"""


def run_effective_dates(occurred_at, reverse, post, on_file):
    """Return one run's own date, and a date for each layer it will post.

    `occurred_at` is when the fact this run is reacting to happened. It is
    what withdraws a superseded layer and what everything posted beside it
    starts from, so one key's versions tile: each ends exactly where the next
    begins, with no gap and no overlap.

    A source's own date is used for one case only — a source this batch has
    never costed before, which is the whole of the March-applied,
    April-posted case. The test is the *source*, not the key, because a run
    also gives an already-posted source a new key whenever it re-targets its
    cost: a cohort layer becoming a `cohort_sale`, a cell's share becoming
    production loss at a germination close or a freeze, a pool share reaching
    a block, a block's unit becoming a named plant. Dating those by the source
    would back-date them behind the layer they supersede and leave the two
    live at once. A source with no dated record of its own falls back to the
    run's date.

    The run's date is never earlier than the layers it is withdrawing. Facts
    recorded in the order they happened never reach that clamp; one recorded
    out of order would otherwise leave two versions of a key live at once, and
    a year-end reader would count both. The clamp is lossy — it files an
    earlier fact under a later date — so `CostAllocationRun.occurred_at` keeps
    the fact's own date beside it, and `bookkeeping.services._clamped_across`
    reads the two together to flag a year the clamp has moved cost out of.
    """
    withdrawn = [row.effective_at for row in reverse]
    effective = max([occurred_at, *withdrawn]) if withdrawn else occurred_at
    return effective, [
        effective if (spec['source_type'], spec['source'].pk) in on_file
        else (spec['source_occurred_at'] or effective)
        for spec in post
    ]
