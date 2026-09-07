"""Reading what points at one catalog record.

Both catalog corrections have to answer this before they do anything. A merge
asks so it can move the references onto the survivor; a replacement asks so it
can say what is staying behind on the record being superseded. The question is
the same one either way — which rows name this record, through which field —
so it is asked in one place and the two corrections differ only in what they
then do with the answer.
"""

#: How many affected records a preview names before it just counts them.
PREVIEW_EXAMPLES = 5


def incoming_relations(record):
    """Return every relation that points at this kind of catalog record."""
    return list(record._meta.related_objects)  # pylint: disable=protected-access


def rows(model):
    """Return every row of a model, past whatever its default manager hides."""
    return model._base_manager.all()  # pylint: disable=protected-access


def pointing_at(relation, record):
    """Return the related records naming this one through this relation."""
    return rows(relation.related_model).filter(**{relation.field.name: record})


def incoming_references(record):
    """Return what points at this record, one entry per incoming relation.

    Entries are keyed by the field rather than the model, because a model may
    name the same catalog record twice — a requisition's preferred supplier is
    not the supplier it was ordered from — and the two move separately.
    """
    references = []
    for relation in incoming_relations(record):
        related = pointing_at(relation, record)
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
