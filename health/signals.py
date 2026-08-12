"""Install useful health catalogs when Nursery mode is enabled."""

from django.db.models.signals import post_save
from django.dispatch import receiver

from workspaces.models import Workspace

from .models import HealthDiagnosis, HealthObservationType


OBSERVATION_TYPES = (
    ('pest-signs', 'Pest signs'),
    ('disease-symptoms', 'Disease symptoms'),
    ('physical-damage', 'Physical damage'),
    ('vigor-stress', 'Vigor or stress'),
    ('environmental', 'Environmental issue'),
)

DIAGNOSES = (
    ('unknown-pest', 'Unknown pest', HealthDiagnosis.Category.PEST),
    ('unknown-disease', 'Unknown disease', HealthDiagnosis.Category.DISEASE),
    ('physical-damage', 'Physical damage', HealthDiagnosis.Category.DAMAGE),
    ('low-vigor', 'Low vigor', HealthDiagnosis.Category.VIGOR),
    ('environmental-stress', 'Environmental stress', HealthDiagnosis.Category.OTHER),
)


def ensure_health_catalogs(workspace):
    """Idempotently create conservative catalogs for a Nursery workspace."""
    if workspace.mode != Workspace.Mode.NURSERY:
        return
    for order, (code, name) in enumerate(OBSERVATION_TYPES):
        HealthObservationType.objects.get_or_create(
            workspace=workspace, code=code,
            defaults={'name': name, 'display_order': order},
        )
    for order, (code, name, category) in enumerate(DIAGNOSES):
        HealthDiagnosis.objects.get_or_create(
            workspace=workspace, code=code,
            defaults={
                'name': name, 'category': category, 'display_order': order,
            },
        )


@receiver(post_save, sender=Workspace)
def workspace_saved(sender, instance, **_kwargs):  # pylint: disable=unused-argument
    """Install catalogs when a workspace becomes a Nursery."""
    ensure_health_catalogs(instance)
