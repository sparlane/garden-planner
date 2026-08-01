"""URL routing for workspace settings."""

from django.urls import path

from .rest import CurrentWorkspaceView


urlpatterns = [
    path('workspace/', CurrentWorkspaceView.as_view(), name='current-workspace'),
]
