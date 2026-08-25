"""REST resources for suppliers."""
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import routers, serializers, viewsets

from tax.ird import normalize_ird_number, validate_ird_number
from workspaces.scoping import CurrentWorkspaceViewSetMixin

from .models import Supplier


class SupplierSerializer(serializers.ModelSerializer):
    """
    Serializer for a Supplier
    """
    class Meta:
        model = Supplier
        fields = [
            'pk', 'name', 'address', 'gst_status', 'gst_number',
            'website', 'notes', 'is_system_default',
        ]
        read_only_fields = ['is_system_default']

    def validate(self, attrs):
        """Return GST identity contradictions as ordinary API field errors."""
        status = attrs.get(
            'gst_status', getattr(self.instance, 'gst_status', Supplier.GstStatus.UNKNOWN),
        )
        number = attrs.get(
            'gst_number', getattr(self.instance, 'gst_number', ''),
        )
        if status != Supplier.GstStatus.REGISTERED:
            if number:
                raise serializers.ValidationError({
                    'gst_number': 'Only a GST-registered supplier has a GST number.',
                })
            return attrs
        if not number:
            raise serializers.ValidationError({
                'gst_number': 'A GST-registered supplier needs its GST number.',
            })
        try:
            number = normalize_ird_number(number)
            validate_ird_number(number)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({'gst_number': exc.messages}) from exc
        attrs['gst_number'] = number
        return attrs


class SupplierViewSet(
    CurrentWorkspaceViewSetMixin,
    viewsets.ModelViewSet,
):  # pylint: disable=too-many-ancestors
    """
    ViewSet of Suppliers
    """
    queryset = Supplier.objects.all()
    serializer_class = SupplierSerializer


router = routers.DefaultRouter()
router.register(r'supplier', SupplierViewSet)
