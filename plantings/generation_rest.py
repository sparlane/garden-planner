"""Attach each tray sowing to the fill of the tray it went into.

This sits beside `batch_rest`, `harvest_rest`, and `lifecycle_rest` for the same
reason they do: the seed-tray sowing serializer already carries the cell,
capacity, batch, and stock rules, and the generation rule is a fourth concern
rather than more of any of those.
"""

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import models
from django.shortcuts import get_object_or_404
from rest_framework import serializers

from seedtrays.generations import require_open_generation
from seedtrays.models import SeedTray, SeedTrayGeneration


def _model_errors(error):
    """Translate Django validation into field-friendly REST errors."""
    if hasattr(error, 'message_dict'):
        return error.message_dict
    return error.messages


class TrayGenerationFilterMixin:
    """Show the tray as it is now, with its earlier fills behind a filter.

    Cleaning a tray archives what was in it. That archive is this filter and
    nothing else: no row is deleted, and `history=true` or an explicit
    `generation` brings any closed fill straight back. Sowings recorded before
    generations existed have no fill to hide behind, so they stay visible.
    """

    #: How this viewset reaches a generation from the rows it lists.
    generation_lookup = 'generation'
    _parent_seed_tray = None

    def get_parent_seed_tray(self):
        """Resolve the nested tray inside the current workspace."""
        if self._parent_seed_tray is None:
            self._parent_seed_tray = get_object_or_404(
                SeedTray,
                pk=self.kwargs['seed_tray_pk'],
                workspace=self.get_current_workspace(),
            )
        return self._parent_seed_tray

    def filter_by_generation(self, queryset, tray):
        """Narrow one tray's rows to the fill the client asked to see."""
        query = self.request.query_params
        requested = query.get('generation')
        if requested is not None:
            try:
                generation = int(requested)
            except ValueError as exc:
                raise serializers.ValidationError({
                    'generation': 'Use an integer generation ID.',
                }) from exc
            return queryset.filter(**{self.generation_lookup: generation})
        history = query.get('history')
        if history is not None:
            if history not in {'true', 'false'}:
                raise serializers.ValidationError({'history': 'Use true or false.'})
            if history == 'true':
                return queryset
        active = SeedTrayGeneration.objects.filter(
            tray=tray,
            status=SeedTrayGeneration.Status.OPEN,
        ).values_list('pk', flat=True)
        visible = models.Q(**{f'{self.generation_lookup}__in': list(active)})
        # A sowing recorded before generations existed has no fill to hide
        # behind, so hiding it would lose it rather than archive it.
        unknown = models.Q(**{f'{self.generation_lookup}__isnull': True})
        return queryset.filter(visible | unknown)


class TrayGenerationSowingSerializerMixin:  # pylint: disable=too-few-public-methods
    """Keep a tray sowing on one fill of one tray for life."""

    def _validate_generation(self, data):
        """Attach this sowing to the fill of the tray it is going into.

        A client that names no generation gets the tray's open one, because
        there is only ever one and asking twice would invite the two answers to
        disagree. A tray with no open fill is refused rather than guessed at:
        seed sown into media nobody recorded has no cost to inherit.
        """
        if self.instance is not None:
            self._require_same_generation(data)
            return
        seed_tray = data.get('seed_tray')
        if seed_tray is None:
            if data.get('generation') is not None:
                raise serializers.ValidationError({
                    'generation': 'A generation belongs to a tray; name the tray too.',
                })
            return
        generation = data.get('generation')
        if generation is None:
            data['generation'] = self._open_generation(seed_tray)
            return
        self._require_usable_generation(generation, seed_tray)

    def _require_same_generation(self, data):
        """An existing sowing keeps the fill it was made into."""
        if 'generation' in data and data['generation'] != self.instance.generation:
            raise serializers.ValidationError({
                'generation': 'Cannot move a sowing between tray generations.',
            })

    @staticmethod
    def _open_generation(seed_tray):
        """Return the tray's open fill, reported as a serializer error."""
        try:
            return require_open_generation(seed_tray)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(_model_errors(exc)) from exc

    @staticmethod
    def _require_usable_generation(generation, seed_tray):
        """Reject a fill of another tray, or one that has been cleaned."""
        if generation.tray_id != seed_tray.pk:
            raise serializers.ValidationError({
                'generation': f'Generation {generation.code} belongs to another tray.',
            })
        if generation.status != SeedTrayGeneration.Status.OPEN:
            raise serializers.ValidationError({
                'generation': f'Generation {generation.code} has been cleaned.',
            })
