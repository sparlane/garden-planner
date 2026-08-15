"""Shared report envelopes, pagination, decimal rendering, and CSV exports."""

import csv
import json
from dataclasses import dataclass, field
from decimal import Decimal
from io import StringIO

from django.core.paginator import EmptyPage, Paginator
from django.http import HttpResponse
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response


REPORT_VERSION = 'nursery-reports.v1'
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


def decimal_string(value, places):
    """Render a decimal at a stable scale without passing floats through APIs."""
    if value is None:
        return None
    return f'{Decimal(value):.{places}f}'


def normalized_filters(serializer):
    """Return validated filters in a JSON-safe, stable representation."""
    result = {}
    for key, value in serializer.validated_data.items():
        if key in {'page', 'page_size'}:
            continue
        if value in (None, '', (), []):
            continue
        if hasattr(value, 'isoformat'):
            value = value.isoformat()
        result[key] = value
    return result


@dataclass
class Report:  # pylint: disable=too-many-instance-attributes
    """One fully calculated report shared by JSON and CSV renderers."""

    name: str
    filters: dict
    columns: tuple
    rows: list
    totals: dict = field(default_factory=dict)
    reconciliation: dict = field(default_factory=dict)
    data_quality: list = field(default_factory=list)
    generated_at: object = field(default_factory=timezone.now)
    version: str = REPORT_VERSION


def _positive_integer(params, name, default, maximum=None):
    raw = params.get(name)
    if raw in (None, ''):
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValidationError({name: 'Use a positive integer.'}) from exc
    if value <= 0:
        raise ValidationError({name: 'Use a positive integer.'})
    if maximum is not None:
        value = min(value, maximum)
    return value


def report_response(request, report):
    """Paginate rows without changing the separately calculated report totals."""
    page_size = _positive_integer(
        request.query_params, 'page_size', DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE,
    )
    page_number = _positive_integer(request.query_params, 'page', 1)
    paginator = Paginator(report.rows, page_size)
    try:
        page = paginator.page(page_number)
    except EmptyPage as exc:
        raise ValidationError({'page': 'That page contains no results.'}) from exc

    def page_url(number):
        if number is None:
            return None
        params = request.query_params.copy()
        params['page'] = number
        params['page_size'] = page_size
        return request.build_absolute_uri(f'{request.path}?{params.urlencode()}')

    return Response({
        'report': report.name,
        'version': report.version,
        'generated_at': report.generated_at,
        'filters': report.filters,
        'totals': report.totals,
        'reconciliation': report.reconciliation,
        'data_quality': report.data_quality,
        'count': paginator.count,
        'next': page_url(page.next_page_number() if page.has_next() else None),
        'previous': page_url(
            page.previous_page_number() if page.has_previous() else None,
        ),
        'results': list(page.object_list),
    })


def csv_response(report):
    """Return a versioned CSV with metadata followed by stable tabular headers."""
    stream = StringIO(newline='')
    writer = csv.writer(stream)
    writer.writerow(('report', 'version', 'generated_at', 'filters'))
    writer.writerow((
        report.name,
        report.version,
        report.generated_at.isoformat(),
        json.dumps(report.filters, sort_keys=True, separators=(',', ':')),
    ))
    writer.writerow(())
    writer.writerow(report.columns)
    for row in report.rows:
        writer.writerow([
            json.dumps(row.get(column), sort_keys=True, separators=(',', ':'))
            if isinstance(row.get(column), (dict, list)) else row.get(column)
            for column in report.columns
        ])
    response = HttpResponse(stream.getvalue(), content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="{report.name}-v1.csv"'
    response['X-Report-Version'] = report.version
    response['X-Report-Generated-At'] = report.generated_at.isoformat()
    return response
