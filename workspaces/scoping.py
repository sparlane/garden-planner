"""Reusable REST helpers for the single-workspace deployment boundary."""

from django.db.models import Q
from rest_framework.exceptions import PermissionDenied

from common.retirement import RetirableModel

from .models import Workspace, get_current_workspace


class CurrentWorkspaceSerializerMixin:  # pylint: disable=too-few-public-methods
    """Limit configured relationship fields to the current workspace."""

    workspace_field_lookups = {}

    def get_fields(self):
        """Apply current-workspace filters to configured related fields."""
        fields = super().get_fields()
        workspace = get_current_workspace()
        for field_name, workspace_lookup in self.workspace_field_lookups.items():
            field = fields[field_name]
            queryset = getattr(field, 'queryset', None)
            if queryset is not None:
                queryset = queryset.filter(**{workspace_lookup: workspace})
                field.queryset = self._offerable(queryset, field_name)
        return fields

    def _offerable(self, queryset, field_name):
        """Stop a retired catalog record being chosen again.

        Retirement is what takes a record out of the selectors, so the rule
        belongs beside the workspace filter rather than in each of the fifty
        serializers that name one. A record already pointing at a retired
        entry keeps it, because saving an unrelated correction must not
        silently repoint the record at something else.
        """
        if not issubclass(queryset.model, RetirableModel):
            return queryset
        current = getattr(self.instance, f'{field_name}_id', None)
        if current is None:
            return queryset.filter(active=True)
        return queryset.filter(Q(active=True) | Q(pk=current))


class CurrentWorkspaceViewSetMixin:
    """Scope reads and bind direct creates to the current workspace."""

    workspace_lookup = 'workspace'
    bind_workspace_on_create = True
    _current_workspace = None

    def get_current_workspace(self):
        """Resolve and cache the configured workspace for this request."""
        if self._current_workspace is None:
            self._current_workspace = get_current_workspace()
        return self._current_workspace

    def get_queryset(self):
        """Return records belonging to the configured workspace."""
        return super().get_queryset().filter(
            **{self.workspace_lookup: self.get_current_workspace()},
        )

    def perform_create(self, serializer):
        """Bind directly owned records to the configured workspace."""
        if self.bind_workspace_on_create:
            serializer.save(workspace=self.get_current_workspace())
        else:
            super().perform_create(serializer)


class RequireWorkspaceModeMixin:  # pylint: disable=too-few-public-methods
    """Serve a route only while the workspace presents the profile it belongs to.

    The mode is a presentation choice over one set of records, so this refuses
    a request rather than hiding data: a Garden workspace has plants, it simply
    has no nursery to run them through. Enforcing it on the server as well as
    in the navigation keeps a bookmarked or scripted URL honest.
    """

    required_workspace_modes = ()

    def get_required_workspace_modes(self):
        """Return the profiles permitted for the current view action."""
        return self.required_workspace_modes

    def initial(self, request, *args, **kwargs):
        """Check the profile before the view does any work for the request."""
        super().initial(request, *args, **kwargs)
        workspace = get_current_workspace()
        required_workspace_modes = self.get_required_workspace_modes()
        if workspace.mode not in required_workspace_modes:
            profiles = ' or '.join(
                Workspace.Mode(mode).label for mode in required_workspace_modes
            )
            raise PermissionDenied(
                f'This feature is available in the {profiles} profile.',
            )
