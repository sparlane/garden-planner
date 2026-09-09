"""Everything a descriptive catalog record is corrected by, in one place.

Retiring, merging and warning about a duplicate are three answers to one
question -- this record is wrong, or is one too many -- and a collection
offering any of them should offer all three. A merge route with no duplicate
warning only ever cleans up after a duplicate nobody was told they were making;
a warning with no merge route names one nobody can then act on; and either
without retirement leaves the loser of the correction in every selector.

So the three are named together rather than listed at each collection, and a
new catalog gets the whole correction vocabulary by saying what it is. The
workspace scoping stays at the call site, because it is not a catalog rule: it
is the deployment boundary every collection sits inside, catalog or not.

This is the mixin for a record that only describes something. A record owning a
stock identity of its own -- a seed catalog entry, a tray model -- is corrected
by replacement instead of merging, for the reasons ``common.replacement`` gives,
so it composes ``ReplaceableViewSetMixin`` in place of the merge half.
"""

from .duplicates import DuplicateWarningViewSetMixin
from .merging import MergeableViewSetMixin
from .retirement import RetirableViewSetMixin


class CatalogViewSetMixin(  # pylint: disable=too-few-public-methods
    DuplicateWarningViewSetMixin,
    MergeableViewSetMixin,
    RetirableViewSetMixin,
):
    """Serve one descriptive catalog collection and every correction it takes.

    The three parts do not overlap -- two add an action and one narrows the
    collection -- so the order they are written in here carries no meaning
    beyond reading in the order an operator meets them.
    """
