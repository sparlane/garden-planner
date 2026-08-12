"""Immutable nursery health evidence and its reviewed affected set."""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from workspaces.models import WorkspaceOwnedModel


class HealthObservationType(WorkspaceOwnedModel):
    """A configurable kind of evidence an operator may observe."""

    code = models.CharField(max_length=64)
    name = models.CharField(max_length=128)
    display_order = models.IntegerField(default=0)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ['display_order', 'name', 'pk']
        constraints = [models.UniqueConstraint(
            fields=['workspace', 'code'], name='health_type_workspace_code_unique',
        )]

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        self.code = self.code.strip().lower()
        if not self.code:
            raise ValidationError({'code': 'A stable code is required.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class HealthDiagnosis(WorkspaceOwnedModel):
    """A configurable pest, disease, damage, or vigor diagnosis."""

    class Category(models.TextChoices):
        """Stable diagnosis groupings used by reports and filters."""

        PEST = 'pest', 'Pest'
        DISEASE = 'disease', 'Disease'
        DAMAGE = 'damage', 'Damage'
        VIGOR = 'vigor', 'Vigor or stress'
        OTHER = 'other', 'Other'

    code = models.CharField(max_length=64)
    name = models.CharField(max_length=128)
    category = models.CharField(max_length=16, choices=Category.choices)
    display_order = models.IntegerField(default=0)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ['category', 'display_order', 'name', 'pk']
        constraints = [models.UniqueConstraint(
            fields=['workspace', 'code'], name='health_diagnosis_workspace_code_unique',
        )]

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        self.code = self.code.strip().lower()
        if not self.code:
            raise ValidationError({'code': 'A stable code is required.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class HealthObservation(WorkspaceOwnedModel):
    """One immutable inspection fact over a frozen affected stock set."""

    class Severity(models.TextChoices):
        """Comparable operational urgency levels."""

        LOW = 'low', 'Low'
        MODERATE = 'moderate', 'Moderate'
        HIGH = 'high', 'High'
        CRITICAL = 'critical', 'Critical'

    observation_type = models.ForeignKey(
        HealthObservationType, on_delete=models.PROTECT, related_name='observations',
    )
    severity = models.CharField(max_length=16, choices=Severity.choices)
    occurred_at = models.DateTimeField(default=timezone.now)
    follow_up_due_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True, default='')
    corrects = models.OneToOneField(
        'self', on_delete=models.PROTECT, null=True, blank=True,
        related_name='correction',
    )
    correction_reason = models.TextField(blank=True, default='')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        editable=False, related_name='+',
    )
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-occurred_at', '-pk']

    def clean(self):
        super().clean()
        errors = {}
        if self.observation_type_id and self.observation_type.workspace_id != self.workspace_id:
            errors['observation_type'] = 'The observation type belongs to another workspace.'
        if self.corrects_id:
            if self.corrects.workspace_id != self.workspace_id:
                errors['corrects'] = 'The corrected observation belongs to another workspace.'
            if not self.correction_reason.strip():
                errors['correction_reason'] = 'A correction reason is required.'
        elif self.correction_reason:
            errors['correction_reason'] = 'A correction reason requires an original observation.'
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError('Health observations are immutable.')
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Health observations cannot be deleted.')


class HealthObservationScope(models.Model):
    """A broad or concrete source the operator selected for an inspection."""

    observation = models.ForeignKey(
        HealthObservation, on_delete=models.PROTECT, related_name='scopes',
    )
    plant = models.ForeignKey(
        'plantings.SpecificPlant', on_delete=models.PROTECT,
        null=True, blank=True, related_name='+',
    )
    cohort = models.ForeignKey(
        'plantings.PlantCohort', on_delete=models.PROTECT,
        null=True, blank=True, related_name='+',
    )
    generation = models.ForeignKey(
        'seedtrays.SeedTrayGeneration', on_delete=models.PROTECT,
        null=True, blank=True, related_name='+',
    )
    batch = models.ForeignKey(
        'plantings.ProductionBatch', on_delete=models.PROTECT,
        null=True, blank=True, related_name='+',
    )
    location = models.ForeignKey(
        'locations.Location', on_delete=models.PROTECT,
        null=True, blank=True, related_name='+',
    )
    label = models.CharField(max_length=255)

    TARGET_FIELDS = ('plant', 'cohort', 'generation', 'batch', 'location')

    class Meta:
        constraints = [models.CheckConstraint(
            condition=models.Q(
                models.Q(plant__isnull=False, cohort__isnull=True, generation__isnull=True, batch__isnull=True, location__isnull=True),
                models.Q(plant__isnull=True, cohort__isnull=False, generation__isnull=True, batch__isnull=True, location__isnull=True),
                models.Q(plant__isnull=True, cohort__isnull=True, generation__isnull=False, batch__isnull=True, location__isnull=True),
                models.Q(plant__isnull=True, cohort__isnull=True, generation__isnull=True, batch__isnull=False, location__isnull=True),
                models.Q(plant__isnull=True, cohort__isnull=True, generation__isnull=True, batch__isnull=True, location__isnull=False),
                _connector=models.Q.OR,
            ),
            name='health_scope_exactly_one_target',
        )]

    @property
    def target(self):
        """Return the one concrete source selected by this row."""
        return next(
            getattr(self, field) for field in self.TARGET_FIELDS
            if getattr(self, f'{field}_id')
        )

    @property
    def target_type(self):
        """Return the API discriminator for the selected source."""
        return next(
            field for field in self.TARGET_FIELDS if getattr(self, f'{field}_id')
        )


class HealthAffectedStock(models.Model):
    """A concrete plant or whole cohort frozen when evidence was confirmed."""

    observation = models.ForeignKey(
        HealthObservation, on_delete=models.PROTECT, related_name='affected_stock',
    )
    plant = models.ForeignKey(
        'plantings.SpecificPlant', on_delete=models.PROTECT,
        null=True, blank=True, related_name='health_observation_memberships',
    )
    cohort = models.ForeignKey(
        'plantings.PlantCohort', on_delete=models.PROTECT,
        null=True, blank=True, related_name='health_observation_memberships',
    )
    quantity = models.PositiveIntegerField()

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    models.Q(plant__isnull=False, cohort__isnull=True, quantity=1),
                    models.Q(plant__isnull=True, cohort__isnull=False, quantity__gte=1),
                    _connector=models.Q.OR,
                ),
                name='health_affected_one_target',
            ),
            models.UniqueConstraint(
                fields=['observation', 'plant'], condition=models.Q(plant__isnull=False),
                name='health_affected_unique_plant',
            ),
            models.UniqueConstraint(
                fields=['observation', 'cohort'], condition=models.Q(cohort__isnull=False),
                name='health_affected_unique_cohort',
            ),
        ]


class HealthObservationDiagnosis(models.Model):
    """A diagnosis explicitly assessed from one observation's evidence."""

    class Certainty(models.TextChoices):
        """Whether evidence suggests or establishes a diagnosis."""

        SUSPECTED = 'suspected', 'Suspected'
        CONFIRMED = 'confirmed', 'Confirmed'

    observation = models.ForeignKey(
        HealthObservation, on_delete=models.PROTECT, related_name='diagnoses',
    )
    diagnosis = models.ForeignKey(
        HealthDiagnosis, on_delete=models.PROTECT, related_name='observation_links',
    )
    certainty = models.CharField(max_length=16, choices=Certainty.choices)

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=['observation', 'diagnosis'], name='health_observation_diagnosis_unique',
        )]


class HealthEvidenceLink(models.Model):
    """An immutable externally hosted photo or supporting document link."""

    observation = models.ForeignKey(
        HealthObservation, on_delete=models.PROTECT, related_name='evidence_links',
    )
    url = models.URLField(max_length=2048)
    label = models.CharField(max_length=255, blank=True, default='')

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=['observation', 'url'], name='health_evidence_observation_url_unique',
        )]
