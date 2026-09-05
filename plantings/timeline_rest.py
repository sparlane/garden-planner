"""The unified plant timeline endpoint.

The timeline is a projection assembled in Python from several tables, so it is
paged after it is built rather than sliced in the database. A plant that has
been potted on every six weeks for four years is a few hundred entries, not a
few hundred thousand, so the whole reading is cheap to assemble and the page
exists to keep the screen readable rather than to keep the query affordable.
"""

from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination

from .timeline import SOURCE_ORDER, timeline_rows


class PlantTimelinePagination(PageNumberPagination):
    """Page one plant's assembled history oldest first."""

    page_size = 100
    page_size_query_param = 'page_size'
    max_page_size = 500


class PlantTimelineViewSetMixin:  # pylint: disable=too-few-public-methods
    """Expose one plant's whole history as a single ordered reading.

    It hangs off the plant rather than standing as a collection of its own
    because a timeline is only ever asked for one plant at a time, and the
    plant's own viewset already scopes the lookup to the current workspace.
    """

    @action(detail=True)
    def timeline(self, request, pk=None):  # pylint: disable=unused-argument
        """Return everything recorded about this plant, oldest first."""
        rows = timeline_rows(self.get_object())
        paginator = PlantTimelinePagination()
        page = paginator.paginate_queryset(rows, request, view=self)
        response = paginator.get_paginated_response(page)
        response.data['sources'] = [
            str(source) for source in sorted(SOURCE_ORDER, key=SOURCE_ORDER.get)
        ]
        return response
