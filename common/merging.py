"""Merging a catalog record into the one it was always a duplicate of.

Retirement takes a mistyped or obsolete record out of the selectors, but it
leaves the history split: half the tomatoes are filed under ``Tomato`` and half
under ``Tomatoe``, and no report can add them up. A merge says the two records
were always the same thing, moves every reference from the duplicate onto the
survivor, and retires the duplicate pointing at where it went.

A merge moves references; it never changes what one of them means. That is the
whole rule, and everything refused below follows from it:

* Two records are interchangeable only while they hang off the same parents, so
  a merge cannot reclassify a variety into another plant. Reparenting is an
  edit somebody makes deliberately, and the catalog screen already does it.
* Nothing may be lost on the way. If moving a reference would collide with one
  the survivor already has, the merge is refused and names the pair, because
  the only alternative would be to drop one of them.
* The duplicate is retired rather than deleted, and keeps the identity every
  posted record was written against. What a posted document says about a
  supplier is its own snapshot, taken when it was issued; a merge moves which
  catalog record the document hangs off and leaves that snapshot alone.

The duplicate is never removed, so a merge is undone by reading ``merged_into``
rather than by guessing which references used to be somewhere else. Merging is
offered only for records that describe something. A record that owns a stock
identity of its own -- a seed catalog entry or a tray model, each holding an
inventory item -- would need its stock moved as well, which is an inventory
operation and not a catalog correction.
"""

from typing import NamedTuple

from django.db import models, transaction
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as RestValidationError
from rest_framework.response import Response

from .retirement import RetirableModel, retirement_errors
from .rest_query import parse_integer

#: How many affected records a preview names before it just counts them.
PREVIEW_EXAMPLES = 5


class MergeableModel(RetirableModel):
    """Abstract catalog identity that can absorb a duplicate of itself."""

    merged_into = models.ForeignKey(
        'self',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        editable=False,
        related_name='merged_from',
        help_text=(
            'The record this duplicate was merged into. It keeps its own '
            'identity so anything written against it still resolves.'
        ),
    )

    class Meta:
        abstract = True

    def merge_extra_errors(self, target):
        """Return reasons beyond the shared rules that refuse this merge."""
        del target
        return []


def merge_errors(source, target):
    """Return why this duplicate cannot be merged into this survivor."""
    if source.pk == target.pk:
        return ['A record cannot be merged into itself.']
    if source.workspace_id != target.workspace_id:
        return ['The records belong to different workspaces.']
    errors = []
    if source.merged_into_id:
        errors.append(f'{source} was already merged into {source.merged_into}.')
    if target.merged_into_id:
        errors.append(f'Merge into {target.merged_into} instead.')
    if not target.active:
        errors.append(f'Restore {target} first.')
    errors += _reparenting_errors(source, target)
    errors += _collision_errors(source, target)
    errors += source.merge_extra_errors(target)
    return errors


def _reparenting_errors(source, target):
    """Refuse a merge that would refile everything under another parent."""
    errors = []
    for name in source.retirement_parents:
        if getattr(source, f'{name}_id') == getattr(target, f'{name}_id'):
            continue
        errors.append(f'Move {source} under {getattr(target, name)} first.')
    return errors


def merge_references(record):
    """Return what points at this record, one entry per incoming relation.

    Entries are keyed by the field rather than the model, because a model may
    name the same catalog record twice — a requisition's preferred supplier is
    not the supplier it was ordered from — and the two move separately.
    """
    references = []
    for relation in _incoming_relations(record):
        related = _pointing_at(relation, record)
        count = related.count()
        if not count:
            continue
        meta = relation.related_model._meta  # pylint: disable=protected-access
        references.append({
            'relation': f'{meta.label_lower}.{relation.field.name}',
            'noun': str(meta.verbose_name if count == 1 else meta.verbose_name_plural),
            'count': count,
            'examples': [str(entry) for entry in related[:PREVIEW_EXAMPLES]],
        })
    return references


def merge_preview(source, target):
    """Describe what a merge would move, and what stands in its way."""
    return {
        'source': {'pk': source.pk, 'label': str(source)},
        'target': {'pk': target.pk, 'label': str(target)},
        'references': merge_references(source),
        'blockers': merge_errors(source, target),
    }


@transaction.atomic
def merge_records(source, target):
    """Move everything naming the duplicate onto the survivor and retire it."""
    source, target = _locked(source, target)
    errors = merge_errors(source, target)
    if errors:
        raise RestValidationError({'into': errors})
    moved = merge_references(source)
    for relation in _incoming_relations(source):
        _pointing_at(relation, source).update(**{relation.field.name: target})
    source.merged_into = target
    source.active = False
    remaining = retirement_errors(source)
    if remaining:
        raise RestValidationError({'into': remaining})
    source.save()
    return moved


def _locked(source, target):
    """Re-read both records under a row lock held for the whole merge."""
    model = type(source)
    locked = model.objects.select_for_update().filter(
        pk__in=[source.pk, target.pk],
    ).in_bulk()
    return locked[source.pk], locked[target.pk]


def _incoming_relations(record):
    """Return every relation that points at this kind of catalog record.

    ``merged_into`` is one of them, so a duplicate merged into this record
    earlier moves on with the rest and the trail stays one hop long.
    """
    return list(record._meta.related_objects)  # pylint: disable=protected-access


def _pointing_at(relation, record):
    """Return the related records naming this one through this relation."""
    return _rows(relation.related_model).filter(**{relation.field.name: record})


def _rows(model):
    """Return every row of a model, past whatever its default manager hides."""
    return model._base_manager.all()  # pylint: disable=protected-access


def _collision_errors(source, target):
    """Refuse a merge that would land two records on one unique key.

    Moving a reference must never drop one, so a duplicate supplier whose
    invoice number the survivor already carries has to be sorted out first.
    Reading the constraints rather than listing the pairs by hand keeps this
    honest as models gain them.
    """
    errors = []
    for relation in _incoming_relations(source):
        field = relation.field.name
        model = relation.related_model
        for key in _unique_keys(model, field, relation.one_to_one):
            errors += _key_collisions(model, field, key, source, target)
    return errors


class UniqueKey(NamedTuple):
    """One uniqueness rule a moving reference has to keep satisfying."""

    #: The fields beside the one being moved that make the key unique.
    others: tuple
    #: The rows the rule applies to, for a partial constraint.
    condition: object


def _unique_keys(model, field, one_to_one):
    """Return the unique keys of this model that this field takes part in."""
    keys = []
    if one_to_one:
        keys.append(UniqueKey((), None))
    meta = model._meta  # pylint: disable=protected-access
    for fields in meta.unique_together:
        if field in fields:
            keys.append(UniqueKey(_beside(fields, field), None))
    for constraint in meta.constraints:
        fields = tuple(getattr(constraint, 'fields', ()) or ())
        if field in fields:
            keys.append(UniqueKey(
                _beside(fields, field), getattr(constraint, 'condition', None),
            ))
    return keys


def _beside(fields, field):
    """Return the key's other fields, which the moving row carries with it."""
    return tuple(name for name in fields if name != field)


def _key_collisions(model, field, key, source, target):
    """Name each moving record whose unique key the survivor already holds."""
    candidates = _rows(model)
    if key.condition is not None:
        candidates = candidates.filter(key.condition)
    errors = []
    for moving in candidates.filter(**{field: source}):
        held = candidates.filter(
            **{field: target},
            **{name: getattr(moving, name) for name in key.others},
        ).first()
        if held is not None:
            errors.append(
                f'{target} already has the {model._meta.verbose_name} '  # pylint: disable=protected-access
                f'{held}, which {moving} would collide with.'
            )
    return errors


class MergeableViewSetMixin:  # pylint: disable=too-few-public-methods
    """Preview and perform a merge of one catalog record into another."""

    @action(detail=True, methods=['get', 'post'])
    def merge(self, request, pk=None):  # pylint: disable=unused-argument
        """Describe or carry out merging this record into the one named.

        The preview and the merge answer the same question, so they read the
        same parameter and report the same refusals; only the method decides
        whether anything moves.
        """
        source = self.get_object()
        target = self._merge_target(request)
        if request.method == 'GET':
            return Response(merge_preview(source, target))
        moved = merge_records(source, target)
        source.refresh_from_db()
        target.refresh_from_db()
        return Response({
            'source': self.get_serializer(source).data,
            'target': self.get_serializer(target).data,
            'moved': moved,
        })

    def _merge_target(self, request):
        """Resolve the survivor named by the request, inside this workspace."""
        if request.method == 'GET':
            into = parse_integer(request.query_params.get('into'), 'into')
        else:
            into = request.data.get('into')
            if into is not None:
                into = parse_integer(str(into), 'into')
        if into is None:
            raise RestValidationError({'into': ['Name the record to merge into.']})
        target = self.get_queryset().filter(pk=into).first()
        if target is None:
            raise RestValidationError({'into': ['No such record.']})
        return target
