"""REST upload, listing, and protected image delivery."""

# DRF supplies the small serializer/viewset methods and inheritance shape.
# pylint: disable=missing-function-docstring,abstract-method,too-many-ancestors

from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import FileResponse
from django.utils.http import content_disposition_header
from rest_framework import routers, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.views import APIView
from rest_framework.response import Response

from workspaces.models import get_current_workspace
from workspaces.scoping import CurrentWorkspaceViewSetMixin

from .models import ImageAttachment
from .archive import export_archive, restore_archive
from .processing import create_attachment


class AttachmentSerializer(serializers.ModelSerializer):
    """Public metadata without exposing a filesystem path."""

    id = serializers.UUIDField(source='public_id', read_only=True)
    target_type = serializers.CharField(read_only=True)
    target_id = serializers.IntegerField(read_only=True)
    content_url = serializers.SerializerMethodField()
    thumbnail_url = serializers.SerializerMethodField()

    class Meta:
        model = ImageAttachment
        fields = [
            'id', 'target_type', 'target_id', 'original_filename',
            'content_type', 'byte_size', 'width', 'height', 'sha256',
            'captured_at', 'created', 'content_url', 'thumbnail_url',
        ]

    def _url(self, attachment, suffix):
        request = self.context.get('request')
        path = f'/attachments/{attachment.public_id}/{suffix}/'
        return request.build_absolute_uri(path) if request else path

    def get_content_url(self, attachment):
        return self._url(attachment, 'content')

    def get_thumbnail_url(self, attachment):
        return self._url(attachment, 'thumbnail')


class AttachmentUploadSerializer(serializers.Serializer):
    """Resolve a multipart upload to one permitted current-workspace target."""

    target_type = serializers.ChoiceField(choices=ImageAttachment.TargetType.choices)
    target_id = serializers.IntegerField(min_value=1)
    image = serializers.FileField()

    def validate(self, attrs):
        workspace = get_current_workspace()
        field = ImageAttachment.TARGET_FIELDS[attrs['target_type']]
        model = ImageAttachment._meta.get_field(field).remote_field.model
        target = model.objects.filter(workspace=workspace, pk=attrs['target_id']).first()
        if target is None:
            raise serializers.ValidationError({
                'target_id': 'No such target exists in this workspace.',
            })
        attrs['target'] = target
        return attrs

    def create(self, validated_data):
        try:
            return create_attachment(
                get_current_workspace(), self.context['request'].user,
                validated_data['target_type'], validated_data['target'],
                validated_data['image'],
            )
        except DjangoValidationError as exc:
            detail = exc.message_dict if hasattr(exc, 'message_dict') else exc.messages
            raise serializers.ValidationError(detail) from exc


class ImageAttachmentViewSet(
    CurrentWorkspaceViewSetMixin, viewsets.ReadOnlyModelViewSet,
):
    """List, create, and privately serve immutable image attachments."""

    queryset = ImageAttachment.objects.all()
    serializer_class = AttachmentSerializer
    lookup_field = 'public_id'
    parser_classes = [MultiPartParser, FormParser]

    def get_queryset(self):
        queryset = super().get_queryset()
        target_type = self.request.query_params.get('target_type')
        target_id = self.request.query_params.get('target_id')
        if target_type is None and target_id is None:
            return queryset
        if target_type not in ImageAttachment.TARGET_FIELDS or not target_id:
            raise serializers.ValidationError({
                'target': 'Provide a valid target_type and target_id together.',
            })
        field = ImageAttachment.TARGET_FIELDS[target_type]
        return queryset.filter(**{f'{field}_id': target_id})

    def create(self, request):
        serializer = AttachmentUploadSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        attachment = serializer.save()
        return Response(
            AttachmentSerializer(attachment, context={'request': request}).data,
            status=status.HTTP_201_CREATED,
        )

    def _file_response(self, attachment, field):
        stored = getattr(attachment, field)
        response = FileResponse(stored.open('rb'), content_type=attachment.content_type)
        response['Content-Disposition'] = content_disposition_header(
            False, attachment.original_filename,
        )
        response['Cache-Control'] = 'private, no-store'
        response['X-Content-Type-Options'] = 'nosniff'
        return response

    @action(detail=True, methods=['get'])
    def content(self, request, public_id=None):  # pylint: disable=unused-argument
        return self._file_response(self.get_object(), 'original')

    @action(detail=True, methods=['get'])
    def thumbnail(self, request, public_id=None):  # pylint: disable=unused-argument
        return self._file_response(self.get_object(), 'thumbnail')


router = routers.DefaultRouter()
router.register('', ImageAttachmentViewSet)


class AttachmentArchiveExportView(APIView):
    """Download the current workspace's versioned photo archive."""

    def get(self, request):  # pylint: disable=unused-argument
        response = FileResponse(
            export_archive(get_current_workspace()),
            content_type='application/zip',
        )
        response['Content-Disposition'] = 'attachment; filename="garden-photos-v1.zip"'
        response['Cache-Control'] = 'private, no-store'
        return response


class AttachmentArchiveRestoreView(APIView):
    """Dry-run or apply a validated photo-only archive restore."""

    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        upload = request.FILES.get('archive')
        if upload is None:
            raise serializers.ValidationError({'archive': 'Choose a photo archive ZIP.'})
        dry_run_value = str(request.data.get('dry_run', 'true')).lower()
        if dry_run_value not in {'true', 'false'}:
            raise serializers.ValidationError({'dry_run': 'Use true or false.'})
        dry_run = dry_run_value == 'true'
        report = restore_archive(
            get_current_workspace(), request.user, upload, dry_run=dry_run,
        )
        response_status = status.HTTP_200_OK
        if not report['valid'] and not dry_run:
            response_status = status.HTTP_400_BAD_REQUEST
        return Response(report, status=response_status)
