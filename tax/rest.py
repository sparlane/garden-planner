"""Read and append the workspace's GST arrangements.

Arrangements are immutable, so this surface offers list, retrieve and create
and nothing else. Correcting one is a create with `supersedes` set, which is a
different act from editing and reads as one in the audit trail.
"""

# pylint: disable=duplicate-code

from django.core.exceptions import ValidationError as DjangoValidationError

from rest_framework import mixins, routers, serializers, viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from workspaces.models import Workspace, get_current_workspace
from workspaces.scoping import (
    CurrentWorkspaceSerializerMixin,
    CurrentWorkspaceViewSetMixin,
    RequireWorkspaceModeMixin,
)

from .models import GstRegistration
from .periods import local_date, registration_history, taxable_period_for
from .services import record_registration


def _model_errors(error):
    """Translate a Django validation error into DRF's field-error shape."""
    return error.message_dict if hasattr(error, 'message_dict') else error.messages


def _run_domain_action(function, *args, **kwargs):
    """Run a domain service, surfacing its errors as DRF field errors."""
    try:
        return function(*args, **kwargs)
    except DjangoValidationError as exc:
        raise ValidationError(_model_errors(exc)) from exc


class GstRegistrationSerializer(CurrentWorkspaceSerializerMixin, serializers.ModelSerializer):
    """One dated arrangement, exactly as it was recorded."""

    workspace_field_lookups = {'supersedes': 'workspace'}
    superseded = serializers.SerializerMethodField()

    class Meta:
        model = GstRegistration
        fields = [
            'pk',
            'registered',
            'effective_from',
            'gst_number',
            'basis',
            'filing_frequency',
            'period_anchor_month',
            'taxable_activity_start',
            'reason',
            'notes',
            'supersedes',
            'superseded',
            'created_by',
            'created',
        ]
        read_only_fields = ['pk', 'superseded', 'created_by', 'created']

    def get_superseded(self, obj):
        """Whether a later correction has replaced this row."""
        return hasattr(obj, 'superseded_by')

    def create(self, validated_data):
        """Append the arrangement through the service that owns the rules."""
        request = self.context.get('request')
        user = getattr(request, 'user', None)
        validated_data.pop('workspace', None)
        return _run_domain_action(
            record_registration,
            get_current_workspace(),
            user,
            **validated_data,
        )


class GstRegistrationViewSet(  # pylint: disable=too-many-ancestors
    RequireWorkspaceModeMixin,
    CurrentWorkspaceViewSetMixin,
    mixins.CreateModelMixin,
    viewsets.ReadOnlyModelViewSet,
):
    """Every arrangement this workspace has recorded, superseded ones included.

    A superseded row is listed rather than hidden. It is what a return filed
    before the correction was filed under, and a report that could not show it
    would be unable to explain why a figure changed.
    """

    required_workspace_modes = (Workspace.Mode.NURSERY,)
    queryset = GstRegistration.objects.select_related(
        'supersedes', 'superseded_by',
    ).order_by('effective_from', 'pk')
    serializer_class = GstRegistrationSerializer
    bind_workspace_on_create = False
    http_method_names = ['get', 'post', 'head', 'options']


class GstStatusView(RequireWorkspaceModeMixin, APIView):
    """What applies right now, for a screen that has to say so in one line."""

    required_workspace_modes = (Workspace.Mode.NURSERY,)
    http_method_names = ['get', 'head', 'options']

    def get(self, request):
        """Return today's arrangement, taxable period, and recorded history."""
        workspace = get_current_workspace()
        today = local_date(workspace, request_now())
        history = registration_history(workspace)
        registration = None
        for row in history:
            if row.effective_from > today:
                break
            registration = row
        period = taxable_period_for(workspace, today, history=history)
        return Response({
            'as_at': today.isoformat(),
            'registered': bool(registration and registration.registered),
            'has_history': bool(history),
            'registration': (
                GstRegistrationSerializer(registration).data if registration else None
            ),
            'taxable_period': _period_payload(period),
        })


def request_now():
    """Return the current instant, isolated so a test can freeze it."""
    from django.utils import timezone  # pylint: disable=import-outside-toplevel
    return timezone.now()


def _period_payload(period):
    """Render a taxable period, or None when the workspace is not registered."""
    if period is None:
        return None
    return {
        'label': period.label,
        'start': period.start.isoformat(),
        'end': period.end.isoformat(),
        'cycle_start': period.cycle_start.isoformat(),
        'cycle_end': period.cycle_end.isoformat(),
        'clipped': period.clipped,
        'frequency': period.frequency,
        'basis': period.basis,
        'registration': period.registration_id,
    }


router = routers.SimpleRouter()
router.register(r'gst/registrations', GstRegistrationViewSet, basename='gstregistration')
