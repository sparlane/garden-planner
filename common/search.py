"""Finding a record in a catalog that has grown past reading straight through.

Every correction starts with finding the record: the crop that stopped being
grown has to be found before it can be retired, the variety that got typed
twice before it can be merged, the tray model with the wrong grid before it can
be replaced. Varieties and seed catalog entries are paginated, so by the time a
catalog is long enough to be worth searching the screen is no longer holding
all of it, and a filter written in the browser can only narrow what it was
sent. So the question is asked of the collection instead.

Search and the duplicate warning are the same question asked by a person and by
a form, and they compare names the same way, because a catalog that said two
names meant the same thing in one box and not in the other would be telling an
operator two different things about one record. ``common.duplicates`` holds
what a name comes down to -- case, punctuation, spacing and a trailing plural
are how one name gets typed twice -- and both read it from there.

A record answers a search when every word of the query begins a word of one of
its names, or when the whole query resembles one closely enough that a merge
would have offered it. The first half is what finds ``Tomato`` for somebody who
typed ``tom``; the second is what finds it for somebody who typed ``Tomatoe``,
which is the search a cleanup starts with.

A record is found by its own name and by every name above and below it in the
catalog, and that is what lets one query narrow a hierarchy rather than break
it. The Plants screen sends the same words to the families, the crops and the
varieties, and what comes back still reads as a tree: searching a variety keeps
the crop and the family it hangs off, and searching a family keeps everything
filed under it. Each model says which names those are, because only it knows
what hangs off it.
"""

from .duplicates import duplicate_reason, normalized


def search_terms(query):
    """Return the words a search is looking for, in the form names compare in."""
    return normalized(query).split()


def answers_search(names, terms):
    """Say whether any of these names answers to every one of these words."""
    return any(_answers(normalized(name), terms) for name in names if name)


def _answers(name, terms):
    """Say whether one name answers to every word of a query.

    A word is matched by beginning a word of the name rather than by appearing
    anywhere inside it: ``rom`` finds ``Roma`` and ``mak`` finds ``Money
    Maker``, while ``oma`` finds neither, because a catalog is read by the
    starts of its words and a search that matched the middles would answer with
    most of it.

    Falling through to the duplicate rule is what makes a mistyped query still
    work. It compares the whole query rather than a word of it, so it only ever
    adds the names somebody would have been offered a merge onto.
    """
    words = name.split()
    if all(any(word.startswith(term) for word in words) for term in terms):
        return True
    return duplicate_reason(' '.join(terms), name) is not None


class CatalogSearchViewSetMixin:  # pylint: disable=too-few-public-methods
    """Narrow a catalog collection to the records somebody is looking for.

    Narrowing rather than ranking: the answer is a shorter catalog, in the
    order the catalog was already in, because what an operator does next is
    read it and correct one of the records in it.
    """

    #: What to load before reading the names a record is found by, so that
    #: searching a hierarchy costs a query per level rather than one per
    #: record. Named here rather than on the queryset because nothing but a
    #: search reads them.
    search_related = ()

    def get_queryset(self):
        """Narrow the collection to what answers ``?search=`` when asked to."""
        queryset = super().get_queryset()
        terms = search_terms(self.request.query_params.get('search', ''))
        if not terms:
            return queryset
        reading = queryset
        # Called with nothing, ``prefetch_related`` clears the list rather than
        # leaving it alone, so a catalog with no names to reach past its own
        # asks for the collection as it stands.
        if self.search_related:
            reading = reading.prefetch_related(*self.search_related)
        found = [
            record.pk
            for record in reading
            if answers_search(record.search_names(), terms)
        ]
        return queryset.filter(pk__in=found)
