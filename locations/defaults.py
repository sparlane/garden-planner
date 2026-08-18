"""The places a household already has, created so nothing is blocked on setup.

A private gardener does not think in receiving docks and dispatch staging, but
the seed, tray, and input workflows all need somewhere to put things. These are
the few ordinary places every household garden has, created once so that a
gardener who never opens the location catalog is not stopped by an empty
picker.

Every entry is keyed on its code, so installing them twice changes nothing.
That is what lets the setup wizard be left and resumed without duplicating
anything.
"""

from .models import Location


#: code, name, type, parent code, and what the place is for.
HOUSEHOLD_LOCATIONS = (
    ('GARDEN', 'Garden', Location.LocationType.SITE, None, 'The garden itself.'),
    ('SHED', 'Shed', Location.LocationType.STORAGE, 'GARDEN', 'Where tools and supplies are kept.'),
    ('SEED-STORE', 'Seed store', Location.LocationType.STORAGE, 'SHED', 'Where seed packets are kept between sowings.'),
    ('POTTING-BENCH', 'Potting bench', Location.LocationType.BENCH, 'SHED', 'Where trays are filled and sown.'),
    ('HOLDING', 'Holding area', Location.LocationType.HOLD, 'GARDEN', 'Where a plant waits until it has somewhere to go.'),
)


def ensure_household_locations(workspace):
    """Idempotently install the ordinary places a household garden needs.

    Returns every location in the set, whether it was created now or already
    existed, so a caller can show the gardener what they have rather than only
    what changed.
    """
    installed = {}
    for order, (code, name, location_type, parent_code, notes) in enumerate(HOUSEHOLD_LOCATIONS):
        location, _created = Location.objects.get_or_create(
            workspace=workspace, code=code,
            defaults={
                'name': name,
                'location_type': location_type,
                'parent': installed.get(parent_code),
                'display_order': order,
                'notes': notes,
            },
        )
        installed[code] = location
    return list(installed.values())
