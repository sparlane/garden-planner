"""
Models related to seeds
"""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models

from inventory.models import (
    InventoryItem,
    InventoryLocation,
    QUANTITY_DECIMAL_PLACES,
    QUANTITY_MAX_DIGITS,
    QuantityCertainty,
    StockLot,
    StockMovement,
    StockReceipt,
)
from plants.models import PlantVariety
from supplies.models import Supplier
from workspaces.models import WorkspaceOwnedModel


class Seeds(WorkspaceOwnedModel):
    """
    Seeds for a specific plant
    """
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT)
    plant_variety = models.ForeignKey(PlantVariety, on_delete=models.PROTECT)
    inventory_item = models.OneToOneField(
        InventoryItem,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='seed_catalog',
    )
    supplier_code = models.CharField(max_length=32, blank=True, null=True)
    url = models.CharField(max_length=1024, blank=True, null=True)
    notes = models.TextField(null=True, blank=True)

    def __str__(self):
        return f"{self.plant_variety} from {self.supplier} ({self.supplier_code})"


class SeedPacket(WorkspaceOwnedModel):
    """
    Specific packet/store of seeds
    """
    seeds = models.ForeignKey(Seeds, on_delete=models.PROTECT)
    stock_lot = models.OneToOneField(
        StockLot,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='seed_packet',
    )
    storage_location = models.OneToOneField(
        InventoryLocation,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='seed_packet',
    )
    purchase_date = models.DateField(null=True, blank=True)
    sow_by = models.DateField(null=True, blank=True)
    empty = models.BooleanField(default=False)
    notes = models.TextField(null=True, blank=True)

    def __str__(self):
        return f"{self.seeds} sow by {self.sow_by}"


class SeedPacketReceiptDraft(WorkspaceOwnedModel):
    """Seed-specific metadata around one ordinary draft stock receipt."""

    seeds = models.ForeignKey(
        Seeds,
        on_delete=models.PROTECT,
        related_name='packet_receipt_drafts',
    )
    receipt = models.OneToOneField(
        StockReceipt,
        on_delete=models.PROTECT,
        related_name='seed_packet_draft',
    )
    storage_location = models.OneToOneField(
        InventoryLocation,
        on_delete=models.PROTECT,
        related_name='seed_packet_draft',
    )
    packet = models.OneToOneField(
        SeedPacket,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='receipt_draft',
    )
    notes = models.TextField(blank=True, default='')
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created', '-pk']

    def __str__(self):
        return f'Seed packet receipt {self.receipt_id}'


class SeedPacketQuantityReconciliation(WorkspaceOwnedModel):
    """Immutable physical packet count and its balancing movement."""

    packet = models.ForeignKey(
        SeedPacket,
        on_delete=models.PROTECT,
        related_name='quantity_reconciliations',
    )
    counted_quantity = models.DecimalField(
        max_digits=QUANTITY_MAX_DIGITS,
        decimal_places=QUANTITY_DECIMAL_PLACES,
        validators=[MinValueValidator(0)],
    )
    quantity_certainty = models.CharField(
        max_length=16,
        choices=QuantityCertainty.choices,
    )
    reconstructed_initial_quantity = models.DecimalField(
        max_digits=QUANTITY_MAX_DIGITS,
        decimal_places=QUANTITY_DECIMAL_PLACES,
        validators=[MinValueValidator(0)],
    )
    movement = models.OneToOneField(
        StockMovement,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='seed_packet_reconciliation',
    )
    reason = models.TextField()
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        editable=False,
        related_name='+',
    )
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created', 'pk']

    def clean(self):
        """Keep counts numeric and inside the packet's workspace."""
        super().clean()
        errors = {}
        if self.quantity_certainty == QuantityCertainty.UNKNOWN:
            errors['quantity_certainty'] = 'A physical count cannot be unknown.'
        if self.packet_id and self.packet.workspace_id != self.workspace_id:
            errors['packet'] = 'The packet belongs to a different workspace.'
        if self.movement_id and self.movement.workspace_id != self.workspace_id:
            errors['movement'] = 'The movement belongs to a different workspace.'
        if not self.reason.strip():
            errors['reason'] = 'A reason is required.'
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError('Packet reconciliations are immutable.')
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Packet reconciliations cannot be deleted.')
