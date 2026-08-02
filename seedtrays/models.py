"""
Models for seed trays
"""
from django.core.exceptions import ValidationError
from django.db import models, transaction

from inventory.models import InventoryItem, InventoryUnit
from workspaces.models import WorkspaceOwnedModel


class SeedTrayModel(WorkspaceOwnedModel):
    """
    A seed tray model used for starting seeds
    """
    identifier = models.CharField(max_length=256)
    inventory_item = models.OneToOneField(
        InventoryItem,
        on_delete=models.PROTECT,
        related_name='seed_tray_model',
    )
    description = models.TextField(null=True, blank=True)

    # Dimensions of the tray itself
    height = models.PositiveIntegerField()
    x_size = models.PositiveIntegerField()
    y_size = models.PositiveIntegerField()

    x_cells = models.PositiveIntegerField()
    y_cells = models.PositiveIntegerField()
    cell_size_ml = models.PositiveIntegerField(help_text='Volume of each cell in milliliters')

    def __str__(self):
        return self.identifier

    def clean(self):
        """Require a compatible serialized tray catalog identity."""
        super().clean()
        if not self.inventory_item_id:
            return
        errors = {}
        if self.inventory_item.workspace_id != self.workspace_id:
            errors['inventory_item'] = 'The inventory item belongs to a different workspace.'
        if self.inventory_item.category != InventoryItem.Category.TRAY:
            errors['inventory_item'] = 'Seed tray models require a tray-category item.'
        if self.inventory_item.tracking_mode != InventoryItem.TrackingMode.SERIALIZED:
            errors['inventory_item'] = 'Seed tray models require a serialized item.'
        if self.inventory_item.base_unit != 'each':
            errors['inventory_item'] = 'Seed tray items must use each as their base unit.'
        if errors:
            raise ValidationError(errors)

    @transaction.atomic
    def save(self, *args, **kwargs):
        """Create the default catalog mapping and lock used relationships."""
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).first()
            if previous and previous.inventory_item_id != self.inventory_item_id:
                has_history = previous.seedtray_set.exists() or previous.inventory_item.stock_history_started_at
                if has_history:
                    raise ValidationError({
                        'inventory_item': 'Cannot change the inventory item after tray or stock history exists.',
                    })
        if not self.inventory_item_id:
            self.inventory_item = InventoryItem.objects.create(
                workspace=self.workspace,
                name=f'Tray model: {self.identifier}',
                category=InventoryItem.Category.TRAY,
                base_unit='each',
                tracking_mode=InventoryItem.TrackingMode.SERIALIZED,
                default_usage_basis=InventoryItem.UsageBasis.MANUAL,
            )
        self.full_clean(validate_unique=False, validate_constraints=False)
        super().save(*args, **kwargs)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['workspace', 'identifier'],
                name='unique_tray_model_identifier_workspace',
            ),
        ]


class SeedTray(WorkspaceOwnedModel):
    """
    A specific seed tray
    """
    model = models.ForeignKey(SeedTrayModel, on_delete=models.PROTECT)
    inventory_unit = models.OneToOneField(
        InventoryUnit,
        on_delete=models.PROTECT,
        related_name='seed_tray',
    )
    created = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(null=True, blank=True)

    def __str__(self):
        return f'Tray {self.model.identifier} created {self.created}'

    def clean(self):
        """Keep a physical tray aligned with its model and unit identity."""
        super().clean()
        errors = {}
        if self.model_id and self.model.workspace_id != self.workspace_id:
            errors['model'] = 'The tray model belongs to a different workspace.'
        if self.inventory_unit_id:
            if self.inventory_unit.workspace_id != self.workspace_id:
                errors['inventory_unit'] = 'The inventory unit belongs to a different workspace.'
            if self.model_id and self.inventory_unit.item_id != self.model.inventory_item_id:
                errors['inventory_unit'] = 'The inventory unit does not match the tray model.'
        else:
            errors['inventory_unit'] = 'Create trays through an audited inventory workflow.'
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        """Validate identity and prevent relationship changes after creation."""
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).first()
            errors = {}
            if previous and previous.model_id != self.model_id:
                errors['model'] = 'Cannot change the model of an existing tray.'
            if previous and previous.inventory_unit_id != self.inventory_unit_id:
                errors['inventory_unit'] = 'Cannot change the unit of an existing tray.'
            if errors:
                raise ValidationError(errors)
        self.full_clean(validate_unique=False, validate_constraints=False)
        super().save(*args, **kwargs)


class SeedTrayCell(models.Model):
    """
    A specific cell in a seed tray
    """
    tray = models.ForeignKey(SeedTray, on_delete=models.CASCADE)
    x_position = models.PositiveIntegerField()
    y_position = models.PositiveIntegerField()

    def __str__(self):
        return f'Cell ({self.x_position}, {self.y_position}) in Tray {self.tray.model.identifier}'

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['tray', 'x_position', 'y_position'], name='unique_cell_per_tray')
        ]
