"""Admin support for the deployment workspace."""

from django.contrib import admin

from .models import Workspace


@admin.register(Workspace)
class WorkspaceAdmin(admin.ModelAdmin):
    """Allow the one deployment workspace to be configured safely."""

    list_display = ('name', 'mode', 'currency_code', 'timezone', 'updated')

    def has_add_permission(self, request):
        """Keep workspace provisioning outside concurrent admin requests."""
        return False

    def has_delete_permission(self, request, obj=None):
        return False
