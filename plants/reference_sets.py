"""Carrying a crop catalog out of one garden and into another.

A reference set is what ``common.reference`` describes: figures a record
arrived with, kept readable beside the figures the gardener now stands behind.
``plants.starters`` is one such set, written out in Python and shipped with the
application. This is the same set shape written down as a document, so a
catalog can also arrive from a garden club, a seed supplier's list, or the
gardener's own second garden -- and the starter set becomes one document among
several rather than the only way a catalog can be filled.

Which is why installing a document and installing the starters are the same
code path. A set that ships in the box and one that arrives in a file differ
only in where the document was written, and any rule that held for one and not
the other -- adopting a name already typed, leaving a retired crop alone,
keeping a measured figure -- would be a rule an operator could get around by
choosing the other route.

Export writes what the catalog says, not what a set once said about it. The
figures worth carrying out of a garden are exactly the ones somebody measured
there, so the provenance is deliberately not exported: it belongs to this
garden and says which numbers this gardener owns. Read from the far end, that
is the same statement -- everything in an arriving document becomes reference
data in the receiving catalog, however hard won it was in the garden it came
from, because it was measured in somebody else's soil and under somebody
else's sky. That is the whole of what ``reference_source`` is claiming, and it
is why an import can never quietly overwrite a figure the receiving gardener
has already answered for themselves.

Only active records are exported. A retired crop is one the gardener put away,
and a document that carried it would install somebody's discarded typo into
the next garden along.

The document is one shape defined once and read in both directions: export
builds it and ``ReferenceSetSerializer`` validates it on the way back in, so
what a garden writes out is what an import accepts.
"""

from django.db import transaction
from django.db.models import Prefetch
from rest_framework import serializers

from common.reference import EMPTY, SOURCE_MAX_LENGTH, install_record

from .models import Plant, PlantFamily, PlantVariety

#: What a set may say about a family. A family carries no planning figures of
#: its own -- it is how the catalog is filed rather than how anything is grown.
FAMILY_FIELDS = ('notes',)

#: What a set may say about a crop: the figures a seed catalogue prints, and
#: the prose beside them.
CROP_FIELDS = (
    'notes',
    'spacing',
    'inter_row_spacing',
    'plants_per_square_foot',
    'germination_days_min',
    'germination_days_max',
    'maturity_days_min',
    'maturity_days_max',
    'maturity_basis',
)

#: The same fields on a variety, because a variety may override any of them.
#: What it does not say it reads through to its crop, which is what
#: ``plants.metadata`` does everywhere else, so a document repeating a crop's
#: spacing on each of its varieties would turn one figure the gardener corrects
#: into four. Export therefore writes only what a variety actually holds.
VARIETY_FIELDS = CROP_FIELDS


class ReferenceVarietySerializer(serializers.ModelSerializer):
    """One variety as a document says it.

    Built from the model so a document is held to the same choices and lengths
    a typed record is: a set may not install a maturity basis the catalog
    screen would refuse.
    """

    class Meta:
        model = PlantVariety
        fields = ['name', *VARIETY_FIELDS]


class ReferenceCropSerializer(serializers.ModelSerializer):
    """One crop and the varieties filed under it."""

    varieties = ReferenceVarietySerializer(many=True, required=False)

    class Meta:
        model = Plant
        fields = ['name', *CROP_FIELDS, 'varieties']


class ReferenceFamilySerializer(serializers.ModelSerializer):
    """One family and the crops filed under it."""

    plants = ReferenceCropSerializer(many=True, required=False)

    class Meta:
        model = PlantFamily
        fields = ['name', *FAMILY_FIELDS, 'plants']


class ReferenceSetSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """A whole document: what the set is called, and the catalog it carries.

    The name is required rather than defaulted, because it is what every
    installed record will answer with when asked where it came from. A document
    that named no set would install records indistinguishable from ones the
    gardener typed, which is the one distinction this whole idea rests on.
    """

    source = serializers.CharField(max_length=SOURCE_MAX_LENGTH)
    families = ReferenceFamilySerializer(many=True)


def _stated(record, fields):
    """Return what this record actually says, dropping what it leaves blank.

    A blank field is nobody having answered yet rather than an answer of
    nothing, so a document that carried it would tell the next catalog that the
    set has an opinion where it has none -- and, being what the set supplied,
    that blank would then be the set's to refresh forever.
    """
    stated = {}
    for name in fields:
        value = getattr(record, name)
        if value not in EMPTY:
            stated[name] = value
    return stated


def _supplied(entry, fields):
    """Return the fields this document entry actually says something about.

    A field the document leaves out is one the set holds no opinion on, and is
    left for the crop above it or for the gardener to answer.
    """
    return {name: entry[name] for name in fields if name in entry}


def export_reference_set(workspace):
    """Return this workspace's active crop catalog as a document.

    Named after the garden it came from, because that is what the receiving
    catalog will show against every record it installs.
    """
    # Three queries whatever the catalog holds: the active records at each
    # level, filtered where they are prefetched so that walking the tree below
    # reads what was already fetched rather than asking again per crop.
    catalog = PlantFamily.objects.filter(workspace=workspace, active=True).order_by('name').prefetch_related(
        Prefetch('plant_set', queryset=Plant.objects.filter(active=True).order_by('name')),
        Prefetch(
            'plant_set__plantvariety_set',
            queryset=PlantVariety.objects.filter(active=True).order_by('name'),
        ),
    )
    families = []
    for family in catalog:
        crops = []
        for plant in family.plant_set.all():
            varieties = [
                {'name': variety.name, **_stated(variety, VARIETY_FIELDS)}
                for variety in plant.plantvariety_set.all()
            ]
            crops.append({
                'name': plant.name,
                **_stated(plant, CROP_FIELDS),
                'varieties': varieties,
            })
        families.append({
            'name': family.name,
            **_stated(family, FAMILY_FIELDS),
            'plants': crops,
        })
    return {'source': workspace.name[:SOURCE_MAX_LENGTH], 'families': families}


@transaction.atomic
def install_reference_set(workspace, document):
    """Install a document into this workspace's catalog.

    All of it or none of it: a gardener told an import failed should not then
    have to work out which half of a file arrived.

    Returns every family, crop and variety the document covers, whether it was
    created now, adopted from what the gardener already had, or left alone, so
    a caller can show what the catalog now holds rather than only what changed.

    Nothing is installed under a record the document reached and found retired,
    for the reason ``common.reference.install_record`` gives: the gardener put
    it away, and a set may not leave an active crop standing under it.
    """
    source = document['source']
    families, plants, varieties = [], [], []
    for entry in document['families']:
        family = install_record(
            PlantFamily, source, entry['name'], _supplied(entry, FAMILY_FIELDS),
            workspace=workspace,
        )
        families.append(family)
        if not family.active:
            continue
        for crop in entry.get('plants', ()):
            plant = install_record(
                Plant, source, crop['name'], _supplied(crop, CROP_FIELDS),
                workspace=workspace, family=family,
            )
            plants.append(plant)
            if not plant.active:
                continue
            for entry_variety in crop.get('varieties', ()):
                varieties.append(install_record(
                    PlantVariety, source, entry_variety['name'],
                    _supplied(entry_variety, VARIETY_FIELDS),
                    workspace=workspace, plant=plant,
                ))
    return families, plants, varieties
