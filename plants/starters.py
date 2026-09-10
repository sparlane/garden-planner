"""The crops a household garden usually holds, so nothing starts from nothing.

A new workspace has an empty catalog, and every workflow in the garden begins
by choosing a variety out of it. Somebody who wants to record that they sowed
a row of carrots should not first have to invent Apiaceae, then Carrot, then
Nantes, and then guess at the spacing. So the ordinary crops arrive already
filed, with the figures a seed catalogue prints.

They arrive as a reference set rather than as ordinary records, which
``common.reference`` explains: what the set said stays readable beside what
each record says, so a figure the gardener has since measured in their own
garden is never overwritten by one printed for somebody else's.

The figures sit on the crop rather than on the variety. A variety reads through
to its plant for anything it does not set itself, which is what
``plants.metadata`` already does everywhere else, so a starter variety says
only what actually differs from the crop above it -- usually its days to
maturity and nothing more. Repeating the crop's spacing on each of its
varieties would turn one figure the gardener corrects into four.

What is written here is the table, and nothing else. Installing it is
``plants.reference_sets``' job, because a set that ships in the box and one a
gardener imports from a file are the same thing seen twice, and every rule
worth having -- adopting a name already typed, leaving a retired crop alone,
keeping a figure somebody measured -- has to hold whichever route a catalog
arrived by. So this module says what the household set contains and then hands
it over as an ordinary document.
"""

from .models import MaturityBasis
from .reference_sets import install_reference_set

#: Names the source on every record this set installs, and is what the catalog
#: screen shows against them. Written as something a gardener would read rather
#: than as an identifier, because a catalog may now arrive from a garden club
#: or another garden as readily as from the set that ships in the box, and one
#: badge has to name all of them.
STARTER_SOURCE = 'Household starters'

#: family -> the crops filed under it. Each crop carries the figures a seed
#: catalogue prints: in-row and between-row spacing in millimetres, how many
#: fit a square foot, days to germinate, days to maturity, and what those days
#: are counted from. A crop raised in a tray and planted out counts from
#: transplanting; one sown where it grows counts from seed.
HOUSEHOLD_CROPS = (
    ('Solanaceae', (
        ('Tomato', 500, 900, 1, 6, 12, 60, 85, MaturityBasis.TRANSPLANTING, (
            ('Money Maker', 70, 80),
            ('Roma', 75, 80),
        )),
        ('Potato', 300, 750, 1, 14, 21, 90, 120, MaturityBasis.SEED, ()),
        ('Capsicum', 450, 600, 1, 8, 14, 70, 90, MaturityBasis.TRANSPLANTING, ()),
    )),
    ('Brassicaceae', (
        ('Broccoli', 450, 600, 1, 5, 10, 60, 80, MaturityBasis.TRANSPLANTING, (
            ('Green Sprouting', 55, 70),
        )),
        ('Cabbage', 450, 600, 1, 5, 10, 70, 100, MaturityBasis.TRANSPLANTING, ()),
        ('Radish', 50, 150, 16, 4, 7, 25, 35, MaturityBasis.SEED, ()),
    )),
    ('Apiaceae', (
        ('Carrot', 50, 300, 16, 10, 21, 70, 90, MaturityBasis.SEED, (
            ('Nantes', 65, 75),
        )),
        ('Parsnip', 100, 400, 9, 14, 28, 110, 140, MaturityBasis.SEED, ()),
    )),
    ('Fabaceae', (
        ('Pea', 50, 450, 8, 7, 14, 60, 70, MaturityBasis.SEED, ()),
        ('Broad Bean', 150, 450, 4, 7, 14, 100, 130, MaturityBasis.SEED, ()),
        ('Dwarf Bean', 100, 450, 4, 7, 12, 55, 70, MaturityBasis.SEED, ()),
    )),
    ('Cucurbitaceae', (
        ('Courgette', 700, 900, 1, 5, 10, 50, 65, MaturityBasis.TRANSPLANTING, ()),
        ('Pumpkin', 900, 1500, 1, 5, 10, 100, 130, MaturityBasis.TRANSPLANTING, ()),
    )),
    ('Alliaceae', (
        ('Onion', 100, 300, 9, 7, 14, 120, 160, MaturityBasis.SEED, ()),
        ('Garlic', 150, 300, 4, 14, 28, 210, 270, MaturityBasis.SEED, ()),
    )),
    ('Asteraceae', (
        ('Lettuce', 250, 300, 4, 5, 10, 45, 60, MaturityBasis.TRANSPLANTING, (
            ('Cos', 55, 65),
        )),
    )),
)


#: The order the figures are written in above, which is the order they read in
#: on the screen: how far apart, how many, how long. Written out rather than
#: derived from the document's crop fields, because these name the columns of
#: the table above: a field inserted into that list would silently re-read
#: every row here as something else. A test keeps the two in step instead. The
#: prose field is absent because the table carries no notes -- a starter crop
#: is one every gardener already knows the name of.
CROP_FIGURES = (
    'spacing', 'inter_row_spacing', 'plants_per_square_foot',
    'germination_days_min', 'germination_days_max',
    'maturity_days_min', 'maturity_days_max', 'maturity_basis',
)


def starter_document():
    """Return the household set as the document any other set would arrive as.

    Building it here rather than writing it out longhand keeps the table above
    compact enough to read as a table, and keeps the two ways a catalog can be
    filled from drifting into two different shapes.
    """
    return {
        'source': STARTER_SOURCE,
        'families': [
            {
                'name': family_name,
                'plants': [
                    {
                        'name': crop[0],
                        **dict(zip(CROP_FIGURES, crop[1:-1])),
                        'varieties': [
                            {
                                'name': variety_name,
                                'maturity_days_min': days_min,
                                'maturity_days_max': days_max,
                            }
                            for variety_name, days_min, days_max in crop[-1]
                        ],
                    }
                    for crop in crops
                ],
            }
            for family_name, crops in HOUSEHOLD_CROPS
        ],
    }


def ensure_starter_crops(workspace):
    """Idempotently install the crops a household garden usually holds.

    Returns every family, crop and variety the set covers, whether it was
    created now, adopted from what the gardener already had, or left alone, so
    a caller can show what the catalog now holds rather than only what changed.
    """
    return install_reference_set(workspace, starter_document())
