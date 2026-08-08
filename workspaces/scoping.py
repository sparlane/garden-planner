"""Reusable REST helpers for the single-workspace deployment boundary."""

from rest_framework.exceptions import PermissionDenied

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
                field.queryset = queryset.filter(
                    **{workspace_lookup: workspace},
                )
        return fields


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

    def initial(self, request, *args, **kwargs):
        """Check the profile before the view does any work for the request."""
        super().initial(request, *args, **kwargs)
        workspace = get_current_workspace()
        if workspace.mode not in self.required_workspace_modes:
            profiles = ' or '.join(
                Workspace.Mode(mode).label for mode in self.required_workspace_modes
            )
            raise PermissionDenied(
                f'This feature is available in the {profiles} profile.',
            )
