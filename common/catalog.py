"""Everything a catalog record is corrected by, in one place.

Retiring, merging and warning about a duplicate are three answers to one
question -- this record is wrong, or is one too many -- and a collection
offering any of them should offer all three. A merge route with no duplicate
warning only ever cleans up after a duplicate nobody was told they were making;
a warning with no merge route names one nobody can then act on; and either
without retirement leaves the loser of the correction in every selector.

Search belongs with them because every one of those corrections starts by
finding the record, and a catalog long enough to need correcting is one nobody
reads straight through any more. It is also the same comparison the warning
makes, as ``common.search`` says, so a collection that can tell an operator two
names mean the same thing should be able to find both when they type one.

So the four are named together rather than listed at each collection, and a
new catalog gets the whole correction vocabulary by saying what it is. The
workspace scoping stays at the call site, because it is not a catalog rule: it
is the deployment boundary every collection sits inside, catalog or not.

There are two of them, because there are two kinds of catalog record. One only
describes something, and two of those can be found to have always been the same
thing, so it merges. The other owns a stock identity of its own -- a seed
catalog entry, a tray model -- and is corrected by replacement instead, for the
reasons ``common.replacement`` gives.

Both keep the warning, and it is the stock catalogs it matters most on: they
are the ones with no merge behind them, so a duplicate there cannot be cleaned
up afterwards at all, only prevented.
"""

from .duplicates import DuplicateWarningViewSetMixin
from .merging import MergeableViewSetMixin
from .replacement import ReplaceableViewSetMixin
from .retirement import RetirableViewSetMixin
from .search import CatalogSearchViewSetMixin


class CatalogViewSetMixin(  # pylint: disable=too-few-public-methods
    CatalogSearchViewSetMixin,
    DuplicateWarningViewSetMixin,
    MergeableViewSetMixin,
    RetirableViewSetMixin,
):
    """Serve one descriptive catalog collection and every correction it takes.

    The parts do not overlap -- two add an action and two narrow the collection
    -- so the order they are written in here carries no meaning beyond reading
    in the order an operator meets them.
    """


class StockCatalogViewSetMixin(  # pylint: disable=too-few-public-methods
    CatalogSearchViewSetMixin,
    DuplicateWarningViewSetMixin,
    ReplaceableViewSetMixin,
    RetirableViewSetMixin,
):
    """Serve one catalog collection that owns a stock identity of its own.

    The same answers with the merge half swapped for replacement, which is the
    whole of the difference between the two kinds of record.
    """
