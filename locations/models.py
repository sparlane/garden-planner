"""The catalog of physical places a workspace uses.

One record describes one physical place, whatever is standing in it. Stock,
serialized assets such as seed trays, and individual plants all refer to the
same row rather than each keeping a private idea of where things are, so a
bench cannot exist twice under two names that drift apart.
"""

# The timestamp, ordering, and workspace-unique-code tail is shared with the
# other workspace-owned catalogs; it is convention, not copied logic.
# pylint: disable=duplicate-code

from django.db import models

from workspaces.models import WorkspaceOwnedModel


class Location(WorkspaceOwnedModel):
    """A physical or operational place that can hold stock."""

    class LocationType(models.TextChoices):
        """Controlled location roles used by stock workflows."""

        RECEIVING = 'receiving', 'Receiving'
        STORAGE = 'storage', 'Storage'
        GROWING = 'growing', 'Nursery or growing area'
        DISPATCH = 'dispatch', 'Customer dispatch'
        QUARANTINE = 'quarantine', 'Quarantine'
        ADJUSTMENT = 'adjustment', 'Adjustment'
        SEED_PACKET = 'seed_packet', 'Seed packet'

    name = models.CharField(max_length=255)
    code = models.CharField(max_length=64)
    location_type = models.CharField(max_length=16, choices=LocationType.choices)
    active = models.BooleanField(default=True)
    notes = models.TextField(blank=True, default='')
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name', 'pk']
        constraints = [
            # Named for the app this model used to live in. The constraint is
            # the one already on the table, so keeping the name means moving
            # the model rewrites no indexes on a live database.
            models.UniqueConstraint(
                fields=['workspace', 'code'],
                name='inventory_location_workspace_code_unique',
            ),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)
