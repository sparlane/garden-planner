"""Reusable REST helpers for the single-workspace deployment boundary."""

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

from common.coded import StableCodeSerializerMixin, normalize_code
from common.retirement import (
    RetirableModel,
    RetirementSerializerMixin,
    prospective_record,
)

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


class CurrentWorkspaceCatalogSerializer(
    RetirementSerializerMixin, serializers.ModelSerializer,
):
    """Validate a workspace-owned catalog record the way its model states it.

    ``ModelSerializer`` checks the fields it was handed and stops there, so a
    rule the model states about the whole record never runs on a REST write,
    and neither does any uniqueness scoped to the workspace -- the workspace is
    not a field the payload carries, so DRF cannot see the key at all. Without
    this, a second setting under a code somebody already used reaches the
    database and comes back as a 500.

    This is the half of the catalog rules that ``common`` may not hold, because
    nothing there may read a workspace. What ``common`` states about a record it
    states from the record alone; what has to be asked of the deployment
    boundary is asked here, beside the retirement exclusion in
    ``CurrentWorkspaceSerializerMixin`` and for the same reason.
    """

    #: The field whose value is unique within the workspace, where one is. A
    #: value already taken is refused by name rather than as an error about the
    #: whole record: it is not a resemblance worth warning about, it is exactly
    #: the record the operator was looking for, so the refusal says which one
    #: holds it and that the answer is to merge into that one.
    unique_in_workspace = None

    def normalize_unique(self, value):
        """Return the value as the uniqueness rule compares it."""
        return value

    def validate(self, attrs):
        """Validate the record this payload would save, in this workspace."""
        attrs = super().validate(attrs)
        workspace = self.instance.workspace if self.instance else get_current_workspace()
        taken = self._taken_errors(workspace, attrs)
        if taken:
            raise serializers.ValidationError(taken)
        candidate = prospective_record(self, attrs)
        candidate.workspace = workspace
        try:
            candidate.full_clean()
        except DjangoValidationError as exc:
            raise serializers.ValidationError(
                exc.message_dict if hasattr(exc, 'message_dict') else exc.messages,
            ) from exc
        return attrs

    def _taken_errors(self, workspace, attrs):
        """Name the record in this workspace already holding this value."""
        field = self.unique_in_workspace
        if field is None or attrs.get(field) is None:
            return {}
        model = self.Meta.model  # pylint: disable=no-member
        taken = model.objects.filter(**{
            'workspace': workspace,
            field: self.normalize_unique(attrs[field]),
        })
        if self.instance is not None:
            taken = taken.exclude(pk=self.instance.pk)
        holder = taken.first()
        if holder is None:
            return {}
        label = model._meta.get_field(field).verbose_name  # pylint: disable=protected-access
        return {field: (
            f'{holder} already uses this {label}. Merge into it rather than '
            f'adding a second.'
        )}


class CodedSettingSerializer(
    StableCodeSerializerMixin, CurrentWorkspaceCatalogSerializer,
):
    """Validate a usage setting other records hold by its stable code.

    The code rule, the activation rule and the catalog's uniqueness are all
    stated elsewhere; what is left is saying that the field holding this
    record's identity is the code.
    """

    unique_in_workspace = 'code'

    def normalize_unique(self, value):
        """Compare a code the way it is stored, which is trimmed and folded."""
        return normalize_code(value)
