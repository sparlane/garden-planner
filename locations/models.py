"""The catalog of physical places a workspace uses.

One record describes one physical place, whatever is standing in it. Stock,
serialized assets such as seed trays, and individual plants all refer to the
same row rather than each keeping a private idea of where things are, so a
bench cannot exist twice under two names that drift apart.
"""

# The timestamp, ordering, and workspace-unique-code tail is shared with the
# other workspace-owned catalogs; it is convention, not copied logic.
# pylint: disable=duplicate-code

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction

from workspaces.models import WorkspaceOwnedModel


#: Separator that makes an ancestor path prefix-matchable without matching a
#: sibling whose id merely starts with the same digits: `/1/` never matches
#: `/12/`.
PATH_SEPARATOR = '/'


def location_full_name(location, names=None):
    """Return a location's name with its ancestors in front of it.

    A bare "Bay 2" is ambiguous across three greenhouses. Callers rendering a
    list should pass `names`, a pk-to-name map for the whole catalog, so that
    naming a page of locations costs one query rather than one per row.
    """
    ancestors = location.ancestor_ids
    if names is None:
        names = dict(
            Location.objects.filter(pk__in=ancestors).values_list('pk', 'name'),
        )
    parts = [names[pk] for pk in ancestors if pk in names]
    parts.append(location.name)
    return ' / '.join(parts)


class Location(WorkspaceOwnedModel):
    """A physical or operational place that can hold stock, trays, or plants."""

    class LocationType(models.TextChoices):
        """Controlled location roles used by stock and growing workflows."""

        SITE = 'site', 'Site'
        GREENHOUSE = 'greenhouse', 'Greenhouse'
        TUNNEL = 'tunnel', 'Tunnel'
        BENCH = 'bench', 'Bench'
        BAY = 'bay', 'Bay'
        RECEIVING = 'receiving', 'Receiving'
        STORAGE = 'storage', 'Storage'
        GROWING = 'growing', 'Nursery or growing area'
        DISPATCH = 'dispatch', 'Customer dispatch'
        HOLD = 'hold', 'Customer hold'
        STAGING = 'staging', 'Dispatch staging'
        QUARANTINE = 'quarantine', 'Quarantine'
        ADJUSTMENT = 'adjustment', 'Adjustment'
        SEED_PACKET = 'seed_packet', 'Seed packet'

    class CapacityBasis(models.TextChoices):
        """The dimension a location's usable space is measured in.

        Unlike dimensions are never compared: a bench counted in trays says
        nothing about how many loose pots fit on it, so each location declares
        which single measure limits it and occupancy is judged only in that one.
        """

        NONE = 'none', 'Not tracked'
        TRAYS = 'trays', 'Trays'
        CONTAINERS = 'containers', 'Containers'
        PLANTS = 'plants', 'Plants'
        AREA = 'area', 'Area'

    #: Bases that a placement can currently be counted against. `area` is
    #: recorded for planning but never enforced, because nothing in the system
    #: records a footprint to measure against it yet; task 54's containers are
    #: what would give plants a real size.
    ENFORCED_BASES = frozenset({
        CapacityBasis.TRAYS,
        CapacityBasis.CONTAINERS,
        CapacityBasis.PLANTS,
    })

    #: Location types the system creates and retires for itself.
    SYSTEM_TYPES = frozenset({LocationType.SEED_PACKET})

    name = models.CharField(max_length=255)
    code = models.CharField(max_length=64)
    location_type = models.CharField(max_length=16, choices=LocationType.choices)
    parent = models.ForeignKey(
        'self',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='children',
    )
    path = models.CharField(max_length=255, blank=True, default='', db_index=True)
    display_order = models.IntegerField(default=0)
    capacity_basis = models.CharField(
        max_length=16,
        choices=CapacityBasis.choices,
        default=CapacityBasis.NONE,
    )
    capacity_value = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal('0'))],
    )
    active = models.BooleanField(default=True)
    notes = models.TextField(blank=True, default='')
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['display_order', 'name', 'pk']
        constraints = [
            # Named for the app this model used to live in. The constraint is
            # the one already on the table, so keeping the name means moving
            # the model rewrites no indexes on a live database.
            models.UniqueConstraint(
                fields=['workspace', 'code'],
                name='inventory_location_workspace_code_unique',
            ),
            models.CheckConstraint(
                condition=models.Q(capacity_basis='none', capacity_value__isnull=True) | (~models.Q(capacity_basis='none') & models.Q(capacity_value__isnull=False)),
                name='location_capacity_value_matches_basis',
            ),
            models.CheckConstraint(
                condition=models.Q(capacity_value__isnull=True) | models.Q(capacity_value__gte=0),
                name='location_capacity_value_nonnegative',
            ),
        ]

    def __str__(self):
        return self.name

    @property
    def is_system_managed(self):
        """Return whether a workflow rather than an operator owns this place."""
        return self.location_type in self.SYSTEM_TYPES

    @property
    def ancestor_ids(self):
        """Return this location's ancestors, outermost first.

        Read from `path` rather than walked through `parent`, so checking a
        whole chain costs one query instead of one per level.
        """
        return [int(part) for part in self.path.split(PATH_SEPARATOR) if part][:-1]

    def build_path(self):
        """Return the id path this location has under its current parent."""
        prefix = self.parent.path if self.parent_id else PATH_SEPARATOR
        return f'{prefix}{self.pk}{PATH_SEPARATOR}'

    def descendants(self):
        """Return every location below this one, at any depth."""
        return self.subtree().exclude(pk=self.pk)

    def subtree(self):
        """Return this location together with everything below it.

        An unsaved location has no path, and an empty prefix would match the
        whole catalog, so it deliberately matches nothing instead.
        """
        if not self.path:
            return type(self).objects.none()
        return type(self).objects.filter(
            workspace_id=self.workspace_id,
            path__startswith=self.path,
        )

    def clean(self):
        """Keep the hierarchy acyclic, in one workspace, and honestly measured."""
        super().clean()
        errors = {}
        if self.parent_id:
            errors.update(self._parent_errors())
        errors.update(self._capacity_errors())
        if self.is_system_managed and (self.parent_id or self.capacity_basis != self.CapacityBasis.NONE):
            errors['location_type'] = 'System-managed locations take no parent or capacity.'
        if errors:
            raise ValidationError(errors)

    def _parent_errors(self):
        """Reject a parent in another workspace or inside this location."""
        parent = self.parent
        if parent.workspace_id != self.workspace_id:
            return {'parent': 'The parent belongs to a different workspace.'}
        if parent.pk == self.pk:
            return {'parent': 'A location cannot contain itself.'}
        # Compared against the stored path, not the one the new parent implies:
        # the question is whether the proposed parent is already below this
        # location, and only the path it has right now can answer that.
        if self.path and parent.path.startswith(self.path):
            return {'parent': 'A location cannot sit inside itself.'}
        if parent.is_system_managed:
            return {'parent': 'System-managed locations hold no other locations.'}
        return {}

    def _capacity_errors(self):
        """Require a limit exactly when a basis says one is being measured."""
        untracked = self.capacity_basis == self.CapacityBasis.NONE
        if untracked and self.capacity_value is not None:
            return {'capacity_value': 'Leave the capacity blank when it is not tracked.'}
        if not untracked and self.capacity_value is None:
            return {'capacity_value': 'Enter the capacity this basis measures.'}
        return {}

    def save(self, *args, **kwargs):
        """Validate the write, then keep this location's subtree paths true.

        The path is written after the row, because a new location learns the id
        its own path is built from only once it has been inserted.
        """
        self.full_clean()
        previous_path = self.path
        with transaction.atomic():
            super().save(*args, **kwargs)
            self.path = self.build_path()
            if self.path == previous_path:
                return
            type(self).objects.filter(pk=self.pk).update(path=self.path)
            if previous_path:
                self._rewrite_descendant_paths(previous_path)

    def _rewrite_descendant_paths(self, previous_path):
        """Move a whole subtree when its root is reparented.

        Reparenting is allowed because an operator who filed a bench under the
        wrong greenhouse should be able to correct it without losing the history
        attached to it.
        """
        moved = type(self).objects.select_for_update().filter(
            workspace_id=self.workspace_id,
            path__startswith=previous_path,
        ).exclude(pk=self.pk)
        for descendant in moved:
            descendant.path = f'{self.path}{descendant.path[len(previous_path):]}'
            type(self).objects.filter(pk=descendant.pk).update(path=descendant.path)
