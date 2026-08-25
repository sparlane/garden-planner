"""Filesystem storage whose location remains deployment configuration."""

# FileSystemStorage exposes these cached values as method-shaped descriptors;
# ours must remain dynamic so override_settings and distinct deployments work.
# pylint: disable=invalid-overridden-method

import os

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.utils.deconstruct import deconstructible


@deconstructible
class PrivateAttachmentStorage(FileSystemStorage):
    """Store files below ATTACHMENT_ROOT without ever exposing a base URL."""

    @property
    def base_location(self):
        return settings.ATTACHMENT_ROOT

    @property
    def location(self):
        return os.path.abspath(self.base_location)

    @property
    def base_url(self):
        return None


private_attachment_storage = PrivateAttachmentStorage()
