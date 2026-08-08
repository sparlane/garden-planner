"""Group each tray's existing sowings into one generation flagged for review.

Nothing here is inferred. A tray that already carries sowings gets exactly one
deterministic ``LEGACY-TRAY-<id>-1`` generation so the tray is usable again, and
that generation is flagged ``needs_review`` because the records cannot say
whether those sowings really were one fill: a tray emptied and re-sown before
this feature existed left no trace of the boundary.

Posted input applications are deliberately left alone. Their targets keep a NULL
generation, which reads as "unknown" rather than as an attribution to this fill,
because assigning historical media to a generation the migration invented is the
one thing the task forbids.
"""

from django.db import migrations


REVIEW_STATE = 'needs_review'
MIGRATION_REASON = 'Migrated from sowings recorded before tray generations existed.'


def _review_details(tray_id, sowings, orphan_target_count):
    """Describe what an operator has to confirm before this fill can be used."""
    notes = [
        f'{len(sowings)} sowing(s) recorded before tray generations existed were '
        f'grouped into one fill of tray #{tray_id}. Confirm they went into the '
        'same media, and split them if the tray was emptied between them.',
    ]
    if orphan_target_count:
        notes.append(
            f'{orphan_target_count} posted input-application target(s) name cells '
            f'of tray #{tray_id} with no recorded generation. Their media is not '
            'attributed to this fill and no cost has been assigned to these '
            'seedlings; reconcile them explicitly if that media belongs here.'
        )
    return '\n'.join(notes)


def create_legacy_generations(apps, _schema_editor):
    """Give every tray with existing sowings one reviewable open generation."""
    generation_model = apps.get_model('seedtrays', 'SeedTrayGeneration')
    event_model = apps.get_model('seedtrays', 'SeedTrayGenerationEvent')
    sowing_model = apps.get_model('plantings', 'SeedTrayPlanting')
    target_model = apps.get_model('applications', 'InputApplicationTarget')

    tray_ids = sowing_model.objects.filter(
        seed_tray__isnull=False,
        generation__isnull=True,
    ).values_list('seed_tray_id', flat=True).distinct().order_by('seed_tray_id')

    for tray_id in list(tray_ids):
        sowings = list(
            sowing_model.objects
            .filter(seed_tray_id=tray_id, generation__isnull=True)
            .order_by('planted', 'pk')
        )
        orphan_target_count = target_model.objects.filter(
            seed_tray_cell__tray_id=tray_id,
            seed_tray_generation__isnull=True,
        ).count()
        generation, created = generation_model.objects.get_or_create(
            workspace_id=sowings[0].workspace_id,
            code=f'LEGACY-TRAY-{tray_id}-1',
            defaults={
                'tray_id': tray_id,
                'sequence': 1,
                'status': 'open',
                'origin': 'legacy',
                'review_state': REVIEW_STATE,
                'review_details': _review_details(
                    tray_id,
                    sowings,
                    orphan_target_count,
                ),
                'opened_at': sowings[0].planted,
                'notes': '',
            },
        )
        if created:
            event_model.objects.create(
                generation=generation,
                event_type='opened',
                occurred_at=generation.opened_at,
                reason=MIGRATION_REASON,
            )
        sowing_model.objects.filter(
            pk__in=[sowing.pk for sowing in sowings],
        ).update(generation=generation)


class Migration(migrations.Migration):
    """Backfill legacy generations once every linking column exists."""

    dependencies = [
        ('seedtrays', '0005_seedtraygeneration_seedtraygenerationevent_and_more'),
        ('plantings', '0025_seedtrayplanting_generation_and_more'),
        ('applications', '0002_inputapplicationtarget_seed_tray_generation_and_more'),
    ]

    operations = [
        migrations.RunPython(create_legacy_generations, migrations.RunPython.noop),
    ]
