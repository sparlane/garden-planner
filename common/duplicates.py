"""Warning that the catalog already means what somebody is typing.

Merging cleans up after a duplicate: the history is split, half the tomatoes
are filed under ``Tomato`` and half under ``Tomatoe``, and somebody has to
notice before any report adds up again. This is the same question asked one
step earlier, while the second record is still only half typed, because the
cheapest duplicate to fix is the one that never gets saved.

It warns and never refuses. These catalogs deliberately carry no unique
constraint on a name -- two suppliers really can trade under one -- so the
answer is what the catalog already holds, and what to do about it stays with
the operator.

The rule is the merge rule, read from the same place. Two records are
interchangeable only while they are filed in the same place, which is what
``merge_errors`` refuses a cross-parent merge on, so the candidates are drawn
from where the new record would be filed and ``retirement_parents`` and
``grouping_fields`` name that here as they do there -- the parents it hangs off
and, for a record filed under a plain value with no record behind it, that
value. A warning therefore never points at a record the merge rule would then
reject as a reclassification.

A superseded record is still worth warning about, and is the case this earns
its keep on: somebody who merged ``Tomatoe`` away last season and is typing it
again is recreating exactly the duplicate they cleaned up, so the candidate
carries where it handed off to.

Names are compared in a normalized form rather than exactly, because case,
punctuation, spacing and a trailing plural are how one name gets typed twice.

Not every catalog has a name to compare. A seed catalog entry is one supplier's
variety and no more, so there is nothing typed on it that could resemble
anything: the parents are the whole of what makes two entries the same, and
sharing them is the duplicate rather than the scope one is looked for in. That
is the collection this matters most on, because it is the one with no merge
behind it. Two entries holding one supplier's variety split that seed across
two inventory items, and joining them afterwards would have to move posted
stock, which is what ``common.replacement`` refuses. Here the warning is not a
shortcut to a cleanup, it is the only one there is.
"""

import re

from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as RestValidationError
from rest_framework.response import Response

from .rest_query import parse_integer

#: Anything that is not a letter or a digit separates words rather than
#: distinguishing one name from another.
_SEPARATORS = re.compile(r'[^0-9a-z]+')

#: A normalized name shorter than this has to match exactly. At three letters a
#: single edit is usually a different crop -- ``Pea`` and ``Tea`` -- so below
#: this the allowance would warn about most of a small catalog. Four is where
#: ``Roma`` and ``Romar`` sit, which is the resemblance a merge cleans up.
MINIMUM_FUZZY_LENGTH = 4

#: The most edits two names may be apart however long they are.
MAXIMUM_ALLOWANCE = 3


def normalized(name):
    """Return the form two catalog names are compared in.

    Case, punctuation, spacing and a trailing plural are how the same name gets
    typed twice, so none of them survive here: ``Money-Maker``, ``money maker``
    and ``Money Makers`` all come down to ``money maker``. What is left is the
    part somebody would say out loud, which is what makes two entries the same
    crop.
    """
    words = [word for word in _SEPARATORS.split(name.casefold()) if word]
    return ' '.join(_singular(word) for word in words)


def _singular(word):
    """Drop a trailing s, which is the commonest way one name becomes two.

    Only one character, and only ever this one. Stripping ``es`` as well would
    take ``chives`` to ``chiv`` and stop it matching ``chive``, and every
    further rule invented here is another way for two names to stop looking
    alike rather than start.
    """
    if len(word) > 2 and word.endswith('s'):
        return word[:-1]
    return word


def allowance(left, right):
    """Return how far apart these two names may be and still be one name.

    One edit for every four letters, measured on the shorter name so a long one
    cannot lend its slack to a short one, and never more than three. A short
    name gets none at all, which is what ``MINIMUM_FUZZY_LENGTH`` says.
    """
    return min(MAXIMUM_ALLOWANCE, min(len(left), len(right)) // MINIMUM_FUZZY_LENGTH)


def edit_distance(left, right):
    """Return how many single-character edits separate two names."""
    if left == right:
        return 0
    previous = list(range(len(right) + 1))
    for index, character in enumerate(left, start=1):
        current = [index]
        for position, other in enumerate(right, start=1):
            current.append(min(
                previous[position] + 1,
                current[position - 1] + 1,
                previous[position - 1] + (character != other),
            ))
        previous = current
    return previous[-1]


def duplicate_reason(target, candidate):
    """Say how a name already in the catalog resembles the one being typed."""
    if not target or not candidate:
        return None
    if target == candidate:
        return 'the same name'
    if edit_distance(target, candidate) <= allowance(target, candidate):
        return 'nearly the same name'
    return None


def _handoff(record):
    """Return where a superseded record says the catalog carries on.

    A merge and a replacement both leave one, and a reader arriving at the old
    name is owed the same answer either way, so this reports whichever is set
    without making the caller know which correction happened.
    """
    for relation in ('merged_into', 'replaced_by'):
        if getattr(record, f'{relation}_id', None) is None:
            continue
        target = getattr(record, relation)
        return {'relation': relation, 'pk': target.pk, 'label': str(target)}
    return None


def _candidate(record, reason):
    """Return one warning: which record, how it means this, where it went."""
    return {
        'pk': record.pk,
        'label': str(record),
        'active': record.active,
        'reason': reason,
        'handoff': _handoff(record),
    }


def duplicate_candidates(records, name, field='name'):
    """Return the records already meaning this name, closest resemblance first.

    The whole collection is read rather than filtered in the database, because
    the comparison is on the normalized form and no index holds that. These are
    one workspace's crops and suppliers, and the alternative -- storing a second
    normalized column -- would have to be kept in step with a rule that is
    expected to be tuned.
    """
    target = normalized(name)
    if not target:
        return []
    candidates = []
    for record in records:
        reason = duplicate_reason(target, normalized(getattr(record, field)))
        if reason is None:
            continue
        candidates.append(_candidate(record, reason))
    candidates.sort(key=lambda entry: (entry['reason'] != 'the same name', entry['label']))
    return candidates


def key_duplicate_candidates(records, reason):
    """Return every record in scope, because the scope is the whole question.

    A collection with nothing typed on it has no resemblance left to measure.
    Anything already filed under the parents the new record would have means
    exactly what it means, so they are all reported and there is nothing to
    rank one above another; they read in the order the collection lists them.
    """
    return [_candidate(record, reason) for record in records]


def duplicate_scope(model, params):
    """Return where a duplicate has to be filed too, read from the request.

    Required rather than optional: without it the answer would name records
    from another crop entirely, and the merge the warning is trying to save
    would be refused as a reclassification.

    A parent arrives as the record it is, and a grouping value as the value it
    is, which is the whole of the difference between the two halves.
    """
    scope = {}
    for name in model.retirement_parents:
        value = parse_integer(params.get(name), name)
        if value is None:
            raise RestValidationError({name: [f'Name the {name} to check against.']})
        scope[f'{name}_id'] = value
    for name in model.grouping_fields:
        value = params.get(name)
        if not value:
            raise RestValidationError({name: [f'Name the {name} to check against.']})
        scope[name] = value
    return scope


class DuplicateWarningViewSetMixin:  # pylint: disable=too-few-public-methods
    """Report which records already mean what an operator is entering."""

    #: The field holding the name somebody types, which is not always ``name``.
    #: ``None`` for a collection with no name typed on it, where the parents
    #: are the whole of what makes two records the same.
    duplicate_name_field = 'name'

    #: What a record already under those parents shares with the one being
    #: entered, for a collection with no name to compare. Written out rather
    #: than assembled from ``retirement_parents``, which spells its relations
    #: the way the database does rather than the way an operator reads them.
    duplicate_key_reason = 'the same entry'

    @action(detail=False, methods=['get'])
    def duplicates(self, request):
        """Name what this catalog already holds that means this.

        ``exclude`` is the record being corrected, which is not a duplicate of
        itself; everything else in scope is compared, retired records included,
        because a retired name is still taken.

        A named collection reads the name under whatever it calls the field and
        reports it back under ``name``, because what the answer is about is a
        name however the field holding it is spelled. A collection with no name
        answers from the scope alone, so it reports the candidates and nothing
        that was compared to reach them.
        """
        queryset = self.get_queryset()
        queryset = queryset.filter(**duplicate_scope(queryset.model, request.query_params))
        exclude = parse_integer(request.query_params.get('exclude'), 'exclude')
        if exclude is not None:
            queryset = queryset.exclude(pk=exclude)
        if self.duplicate_name_field is None:
            return Response({
                'candidates': key_duplicate_candidates(
                    queryset, self.duplicate_key_reason,
                ),
            })
        name = request.query_params.get(self.duplicate_name_field, '')
        return Response({
            'name': name,
            'normalized': normalized(name),
            'candidates': duplicate_candidates(
                queryset, name, self.duplicate_name_field,
            ),
        })
