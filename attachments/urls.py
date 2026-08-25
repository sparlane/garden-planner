"""Routes for private image attachments."""

from django.urls import include, path

from .rest import (
    AttachmentArchiveExportView,
    AttachmentArchiveRestoreView,
    router,
)


urlpatterns = [
    path('archive/', AttachmentArchiveExportView.as_view(), name='attachment-archive'),
    path(
        'archive/restore/', AttachmentArchiveRestoreView.as_view(),
        name='attachment-archive-restore',
    ),
    path('', include(router.urls)),
]
