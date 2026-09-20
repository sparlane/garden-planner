# Name the Location a Work Task Points At

## Problem

Two projectors label a location link with `location.full_name`:

```python
targets.append(TargetLink(milestone.location, milestone.location.full_name, '/locations'))
...
links.append(TargetLink(location, location.full_name, '/locations'))
```

`Location` (`locations/models.py:45`) has no `full_name`. The ancestry-qualified name
is `location_full_name` (`locations/models.py:28`), a module function every other
caller uses — `plantings/timeline.py:159`, `plantings/garden_register.py:123`,
`inventory/balance_rest.py:213` — and `full_name` is a `SerializerMethodField` on the
location serializer (`locations/rest.py:42`), which is presumably where the attribute
was remembered from.

So both lines raise `AttributeError: 'Location' object has no attribute 'full_name'`.
`projected_tasks` builds every rule's projections in one pass, so the exception is not
confined to the rule that raised it: the whole work queue fails.

Reaching it takes no configuration. `_milestone_tasks` raises for any approved plan
milestone that names a location, which `plantings/planning.py` sets from the stage
assumption. `_growth_tasks` raises as soon as a stage-review or ready-review group has
a location, which is every plant standing on a bench and every cohort, since a cohort
carries a `location` foreign key directly.

It survived because no test puts a plant or cohort at a location and then reads the
queue: `work/test_projections.py` works from sowings and garden squares, whose links
are labelled from the sowing or the square.

## Impact

The work queue is the nursery's daily list, and it returns a 500 rather than a partial
list, so one plant moved onto a named bench takes out germination checks, maturity
reviews, health follow-ups and everything else with it.

## References

- `work/projections.py:386` — the milestone location link.
- `work/projections.py:462` — the growth-review location link.
- `locations/models.py:28` — `location_full_name`, the real derivation.
- `locations/rest.py:42` — the serializer field the name was taken from.

## Goal

A task that names a location is labelled with that location's full name.

## Implementation

Completed locally, in the same sweep as [task 124](124-exclude-resolved-plants-from-work.md),
because 124's regression tests put plants on a bench and could not run past the
exception.

- Both sites call `location_full_name(location)`.
- `MilestoneLocationLabelTests` in `work/test_projections.py` projects an approved plan
  milestone on `Greenhouse / Bench 2` and asserts that label.
- The growth-review site is covered in `work/test_growth_projections.py`, whose bench
  stands under a greenhouse for the purpose:
  `test_the_bench_is_named_with_the_greenhouse_it_stands_in` for a plant, and
  `CohortGrowthProjectionTests.test_a_standing_block_is_reviewed_on_its_named_bench`
  for a cohort, which carries a location directly and is the other line that raised.

Each label costs one query for the location's ancestry, as `plantings/timeline.py`
notes. A projection names one location per group, not per row, so no names map is
built here; [task 133](133-fix-the-work-projection-n-plus-one.md) passes over the same
function and can batch it if it is worth batching.

## Verification

1. Put a plant on a named bench under a greenhouse, observe a stage with a target age,
   and read the queue: the task's location link reads `Greenhouse / Bench 2`.
2. Run `./manage.py test` and `./check-code.sh`.

## Estimated effort

Half an hour, done. The time was in noticing it: the exception is raised inside
`projected_tasks`, so it reads as the whole queue being broken rather than as one
label.
