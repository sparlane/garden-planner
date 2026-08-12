"""Traceable health history and grouped operational reporting."""

# Report rows intentionally assemble every traceability dimension together.
# pylint: disable=too-many-locals

from collections import Counter

from django.db.models import Q

from plantings.models import (
    GardenRowDirectSowPlanting,
    GardenSquareDirectSowPlanting,
    SeedTrayPlanting,
)

from .models import HealthObservation


def _seed_sources(batch_ids):
    """Return snapshotted seed-product lineage for the selected batches."""
    sources = {}
    for model in (
            SeedTrayPlanting, GardenRowDirectSowPlanting,
            GardenSquareDirectSowPlanting,
    ):
        rows = model.objects.filter(batch_id__in=batch_ids).select_related(
            'seeds_used__seeds__supplier',
        )
        for row in rows:
            packet = row.seeds_used
            sources.setdefault(row.batch_id, {})[packet.pk] = {
                'seed_packet': packet.pk,
                'seed_product': packet.seeds_id,
                'supplier': packet.seeds.supplier_id,
                'supplier_name': packet.seeds.supplier.name,
            }
    return {
        batch_id: list(packets.values())
        for batch_id, packets in sources.items()
    }


def _queryset(workspace, filters):
    queryset = HealthObservation.objects.filter(
        workspace=workspace, correction__isnull=True,
    ).select_related('observation_type').prefetch_related(
        'affected_stock__plant__batch__variety',
        'affected_stock__cohort__batch__variety',
        'scopes__location', 'diagnoses__diagnosis',
        'treatments__application__lines__item', 'follow_ups',
        'quarantine_cases__actions',
    )
    if filters.get('observation_type'):
        queryset = queryset.filter(observation_type_id=filters['observation_type'])
    if filters.get('severity'):
        queryset = queryset.filter(severity=filters['severity'])
    if filters.get('diagnosis'):
        queryset = queryset.filter(diagnoses__diagnosis_id=filters['diagnosis'])
    if filters.get('category'):
        queryset = queryset.filter(diagnoses__diagnosis__category=filters['category'])
    if filters.get('batch'):
        batch_filter = Q(affected_stock__plant__batch_id=filters['batch'])
        batch_filter |= Q(affected_stock__cohort__batch_id=filters['batch'])
        batch_filter |= Q(scopes__batch_id=filters['batch'])
        queryset = queryset.filter(batch_filter)
    if filters.get('location'):
        queryset = queryset.filter(scopes__location_id=filters['location'])
    if filters.get('treatment'):
        queryset = queryset.filter(treatments__application_id=filters['treatment'])
    if filters.get('outcome'):
        queryset = queryset.filter(
            follow_ups__result=filters['outcome'],
            follow_ups__correction__isnull=True,
        )
    return queryset.distinct()


def health_report(workspace, filters):
    """Return grouped counts plus traceable observation rows."""
    observations = list(_queryset(workspace, filters))
    batch_ids = {
        member.plant.batch_id if member.plant_id else member.cohort.batch_id
        for observation in observations
        for member in observation.affected_stock.all()
    }
    seed_sources = _seed_sources(batch_ids)
    rows = []
    issue_counts = Counter()
    severity_counts = Counter()
    diagnosis_counts = Counter()
    outcome_counts = Counter()
    for observation in observations:
        diagnoses = list(observation.diagnoses.all())
        follow_ups = [row for row in observation.follow_ups.all() if not hasattr(row, 'correction')]
        batches = {}
        affected = []
        for member in observation.affected_stock.all():
            target = member.plant or member.cohort
            batch = target.batch
            batches[batch.pk] = {
                'batch': batch.pk,
                'code': batch.code,
                'variety': batch.variety_id,
                'variety_name': batch.variety.name,
            }
            affected.append({
                'type': 'plant' if member.plant_id else 'cohort',
                'id': target.pk,
                'quantity': member.quantity,
            })
        issue_counts[observation.observation_type.name] += 1
        severity_counts[observation.severity] += 1
        diagnosis_counts.update(row.diagnosis.name for row in diagnoses)
        outcome_counts.update(row.result for row in follow_ups)
        rows.append({
            'observation': observation.pk,
            'occurred_at': observation.occurred_at,
            'observation_type': observation.observation_type.name,
            'severity': observation.severity,
            'diagnoses': [{
                'diagnosis': row.diagnosis_id,
                'name': row.diagnosis.name,
                'category': row.diagnosis.category,
                'certainty': row.certainty,
            } for row in diagnoses],
            'affected': affected,
            'batches': list(batches.values()),
            'seed_sources': [
                source for batch_id in batches for source in seed_sources.get(batch_id, ())
            ],
            'locations': [{
                'location': scope.location_id, 'label': scope.label,
            } for scope in observation.scopes.all() if scope.location_id],
            'treatments': [{
                'treatment': treatment.pk,
                'application': treatment.application_id,
                'status': treatment.application.status,
                'items': [line.item.name for line in treatment.application.lines.all()],
            } for treatment in observation.treatments.all()],
            'outcomes': [{
                'follow_up': row.pk, 'result': row.result,
                'effectiveness': row.effectiveness,
            } for row in follow_ups],
        })
    return {
        'summary': {
            'observations': len(rows),
            'by_issue': dict(issue_counts),
            'by_severity': dict(severity_counts),
            'by_diagnosis': dict(diagnosis_counts),
            'by_outcome': dict(outcome_counts),
        },
        'results': rows,
    }
