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

Installing is keyed on the name, compared the way ``common.duplicates``
compares two names, so asking twice changes nothing and a gardener who already
typed ``Tomatoes`` has it adopted rather than duplicated. That is the same rule
the duplicate warning would have applied had a person been typing.
"""

from common.duplicates import normalized
from common.reference import apply_reference

from .models import MaturityBasis, Plant, PlantFamily, PlantVariety

#: Names the source on every record this module installs.
STARTER_SOURCE = 'household-starters'

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
#: on the screen: how far apart, how many, how long.
CROP_FIGURES = (
    'spacing',
    'inter_row_spacing',
    'plants_per_square_foot',
    'germination_days_min',
    'germination_days_max',
    'maturity_days_min',
    'maturity_days_max',
    'maturity_basis',
)


def _existing(records, name):
    """Return the record already meaning this name, or None.

    Compared the way two catalog names are compared everywhere else, so a
    gardener's ``Tomatoes`` is the set's ``Tomato`` rather than a second one.
    """
    wanted = normalized(name)
    for record in records:
        if normalized(record.name) == wanted:
            return record
    return None


def _install(model, name, values, **scope):
    """Adopt or create one record and write what the set says onto it.

    A retired record is left exactly as it is. Retiring is something the
    gardener chose, and a set that quietly refreshed a crop somebody had put
    away would be arguing with that as surely as one that overwrote a figure
    they had measured. Nothing is installed under it either, which is also what
    keeps the set from leaving an active crop under a retired family.
    """
    record = _existing(model.objects.filter(**scope), name)
    if record is not None and not record.active:
        return record
    if record is None:
        record = model(name=name, **scope)
    apply_reference(record, STARTER_SOURCE, values)
    record.save()
    return record


def ensure_starter_crops(workspace):
    """Idempotently install the crops a household garden usually holds.

    Returns every family, crop and variety the set covers, whether it was
    created now, adopted from what the gardener already had, or left alone, so
    a caller can show what the catalog now holds rather than only what changed.
    """
    families, plants, varieties = [], [], []
    for family_name, crops in HOUSEHOLD_CROPS:
        family = _install(PlantFamily, family_name, {}, workspace=workspace)
        families.append(family)
        if not family.active:
            continue
        for crop in crops:
            plant = _install(
                Plant,
                crop[0],
                dict(zip(CROP_FIGURES, crop[1:-1])),
                workspace=workspace,
                family=family,
            )
            plants.append(plant)
            if not plant.active:
                continue
            for variety_name, days_min, days_max in crop[-1]:
                varieties.append(_install(
                    PlantVariety,
                    variety_name,
                    {'maturity_days_min': days_min, 'maturity_days_max': days_max},
                    workspace=workspace,
                    plant=plant,
                ))
    return families, plants, varieties
