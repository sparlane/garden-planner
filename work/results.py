"""Search existing garden work results without performing the work itself."""

from django.db.models import Q
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import PageNumberPagination

from applications.models import InputApplication
from plantings.models import DirectSownCropEvent, GardenPlanting, Harvest, PlantLifecycleEvent


class ResultPagination(PageNumberPagination):
    """Keep an unfiltered household history bounded and browsable."""

    page_size = 25


def result_options(workspace, request):
    """Return selectable workspace-owned records with canonical internal links."""
    kind = request.query_params.get('kind', 'planting')
    sources = {
        'planting': (GardenPlanting, {}, 'batch__variety', 'recorded_on'),
        'care': (InputApplication, {'status': 'posted'}, 'batch__variety', 'applied_at'),
        'harvest': (Harvest, {'status': 'posted'}, 'batch__variety', 'harvested_at'),
        'move': (DirectSownCropEvent, {'event_type': 'moved', 'reversal__isnull': True}, 'planting__batch__variety', 'occurred_on'),
        'plant_move': (PlantLifecycleEvent, {'event_type': 'transplanted', 'reversal__isnull': True}, 'batch__variety', 'occurred_at'),
    }
    if kind not in sources:
        raise ValidationError({'kind': 'Choose planting, care, movement, or harvest.'})
    model, filters, variety_path, date_field = sources[kind]
    rows = model.objects.filter(workspace=workspace, **filters).select_related(
        variety_path, f'{variety_path}__plant',
    ).order_by(f'-{date_field}', '-pk')
    search = request.query_params.get('search', '').strip()
    if search:
        query = Q(**{f'{variety_path}__name__icontains': search}) | Q(**{f'{variety_path}__plant__name__icontains': search})
        if search.isdigit():
            query |= Q(pk=int(search))
        rows = rows.filter(query)
    paginator = ResultPagination()
    page = paginator.paginate_queryset(rows, request)
    return paginator.get_paginated_response([_option(row, kind, date_field) for row in page])


def _option(row, kind, date_field):
    batch = row.planting.batch if kind == 'move' else row.batch
    crop = str(batch.variety) if batch else 'Whole garden'
    urls = {
        'planting': f'/plantings/garden-register/aggregate-{row.pk}',
        'care': '/applications',
        'harvest': f'/plantings/harvests?batch={row.batch_id}' if kind == 'harvest' else '',
        'move': f'/plantings/garden-register/aggregate-{row.planting_id}' if kind == 'move' else '',
        'plant_move': f'/plantings/plants/{row.plant_id}' if kind == 'plant_move' else '',
    }
    # Individual origins are represented by their plants in the register.
    if kind == 'planting' and row.tracking == GardenPlanting.Tracking.INDIVIDUAL:
        urls['planting'] = '/plantings/garden-register'
    return {
        'app_label': row._meta.app_label,  # pylint: disable=protected-access
        'model': row._meta.model_name,  # pylint: disable=protected-access
        'object_id': row.pk,
        'label': f'{crop} · {getattr(row, date_field).isoformat()} · {kind.replace("_", " ")} {row.pk}'[:255],
        'url': urls[kind],
    }
