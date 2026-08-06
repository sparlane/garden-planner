"""Singleton REST interface for workspace settings."""

from rest_framework import generics, serializers

from .current import get_current_workspace
from .models import Workspace


class WorkspaceSerializer(serializers.ModelSerializer):
    """Serialize the editable profile for the current workspace."""

    class Meta:
        model = Workspace
        fields = [
            'name',
            'mode',
            'currency_code',
            'default_tax_rate',
            'timezone',
            'measurement_system',
            'override_tolerance_percent',
            'override_tolerance_floor',
            'created',
            'updated',
        ]
        read_only_fields = ['created', 'updated']


class CurrentWorkspaceView(generics.RetrieveUpdateAPIView):
    """Retrieve or partially update the deployment workspace."""

    serializer_class = WorkspaceSerializer
    http_method_names = ['get', 'patch', 'head', 'options']

    def get_object(self):
        workspace = get_current_workspace()
        self.check_object_permissions(self.request, workspace)
        return workspace
