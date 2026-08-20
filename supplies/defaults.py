"""A stand-in supplier for stock a Basic Garden workflow received unnamed.

Receiving a seed packet or an item always names a supplier — that is what
the underlying stock lot and its cost provenance are keyed on. A Basic
gardener recording seed they were given or saved should not have to invent
one, so a single system-owned row absorbs the requirement instead. It is
flagged with `is_system_default`, the way `ProductionBatch.code_is_generated`
flags a generated crop code, so an Advanced screen can say plainly that
nobody named this supplier.
"""

from .models import Supplier

#: Shown wherever the system-default supplier is picked or listed.
DEFAULT_SUPPLIER_NAME = 'Unknown / home garden'


def ensure_default_supplier(workspace):
    """Idempotently return this workspace's system-default supplier."""
    supplier, _created = Supplier.objects.get_or_create(
        workspace=workspace,
        is_system_default=True,
        defaults={'name': DEFAULT_SUPPLIER_NAME},
    )
    return supplier
