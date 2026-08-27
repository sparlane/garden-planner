"""REST contract for bookkeeping, assets, and income-year working papers."""

# DRF supplies the small serializer/viewset methods and inheritance shape.
# pylint: disable=missing-class-docstring,missing-function-docstring,too-many-ancestors,unused-argument

import csv
from io import StringIO

from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import HttpResponse
from rest_framework import routers, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from workspaces.scoping import CurrentWorkspaceSerializerMixin, CurrentWorkspaceViewSetMixin

from .models import BookkeepingEntry, DepreciationSchedule, IncomeTaxYear, Liability, StockValuationLine, TaxAsset
from .services import build_report, capture_inventory, finalize_income_year, reverse_entry


def _run(command, *args, **kwargs):
    try:
        return command(*args, **kwargs)
    except DjangoValidationError as exc:
        detail = exc.message_dict if hasattr(exc, 'message_dict') else exc.messages
        raise serializers.ValidationError(detail) from exc


class LiabilitySerializer(CurrentWorkspaceSerializerMixin, serializers.ModelSerializer):
    class Meta:
        model = Liability
        fields = '__all__'
        read_only_fields = ['workspace', 'created']


class EntrySerializer(CurrentWorkspaceSerializerMixin, serializers.ModelSerializer):
    class Meta:
        model = BookkeepingEntry
        fields = '__all__'
        read_only_fields = ['workspace', 'created_by', 'created', 'operation_key', 'reversal_of']


class TaxAssetSerializer(CurrentWorkspaceSerializerMixin, serializers.ModelSerializer):
    class Meta:
        model = TaxAsset
        fields = '__all__'
        read_only_fields = ['workspace', 'created_by', 'created', 'updated']


class ScheduleSerializer(CurrentWorkspaceSerializerMixin, serializers.ModelSerializer):
    class Meta:
        model = DepreciationSchedule
        fields = '__all__'
        read_only_fields = ['workspace', 'created_by', 'created']


class StockLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = StockValuationLine
        fields = '__all__'
        read_only_fields = ['derived', 'provisional', 'created_by', 'created']


class IncomeYearSerializer(CurrentWorkspaceSerializerMixin, serializers.ModelSerializer):
    stock_lines = StockLineSerializer(many=True, read_only=True)
    live_report = serializers.SerializerMethodField()

    class Meta:
        model = IncomeTaxYear
        fields = '__all__'
        read_only_fields = ['workspace', 'status', 'revision', 'supersedes', 'frozen_report', 'finalized_at', 'finalized_by', 'retain_until', 'created']

    def get_live_report(self, instance):
        return instance.frozen_report if instance.status == IncomeTaxYear.Status.FINALIZED else build_report(instance)

    def validate_year_end(self, value):
        if (value.month, value.day) != (3, 31):
            raise serializers.ValidationError('Normal New Zealand income years must end on 31 March.')
        return value


class LiabilityViewSet(CurrentWorkspaceViewSetMixin, viewsets.ModelViewSet):
    queryset = Liability.objects.all()
    serializer_class = LiabilitySerializer


class EntryViewSet(CurrentWorkspaceViewSetMixin, viewsets.ModelViewSet):
    queryset = BookkeepingEntry.objects.select_related('liability', 'reversal_of')
    serializer_class = EntrySerializer
    http_method_names = ['get', 'post', 'head', 'options']

    def perform_create(self, serializer):
        serializer.save(workspace=self.get_current_workspace(), created_by=self.request.user)

    @action(detail=True, methods=['post'])
    def reverse(self, request, pk=None):
        reason = serializers.CharField().run_validation(request.data.get('reason'))
        entry = _run(reverse_entry, self.get_object(), request.user, reason)
        return Response(self.get_serializer(entry).data, status=status.HTTP_201_CREATED)


class TaxAssetViewSet(CurrentWorkspaceViewSetMixin, viewsets.ModelViewSet):
    queryset = TaxAsset.objects.all()
    serializer_class = TaxAssetSerializer

    def perform_create(self, serializer):
        serializer.save(workspace=self.get_current_workspace(), created_by=self.request.user)


class ScheduleViewSet(CurrentWorkspaceViewSetMixin, viewsets.ModelViewSet):
    queryset = DepreciationSchedule.objects.select_related('asset')
    serializer_class = ScheduleSerializer
    http_method_names = ['get', 'post', 'head', 'options']

    def perform_create(self, serializer):
        serializer.save(workspace=self.get_current_workspace(), created_by=self.request.user)


class IncomeYearViewSet(CurrentWorkspaceViewSetMixin, viewsets.ModelViewSet):
    queryset = IncomeTaxYear.objects.prefetch_related('stock_lines')
    serializer_class = IncomeYearSerializer
    http_method_names = ['get', 'post', 'delete', 'head', 'options']

    def perform_create(self, serializer):
        serializer.save(workspace=self.get_current_workspace())

    @action(detail=True, methods=['post'])
    def capture(self, request, pk=None):
        _run(capture_inventory, self.get_object(), request.user)
        return Response(self.get_serializer(self.get_object()).data)

    @action(detail=True, methods=['post'], url_path='stock-lines')
    def add_stock_line(self, request, pk=None):
        serializer = StockLineSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        line = serializer.save(income_year=self.get_object(), created_by=request.user)
        return Response(StockLineSerializer(line).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def finalize(self, request, pk=None):
        confirm = serializers.BooleanField(default=False).run_validation(request.data.get('confirm_zero_opening', False))
        year = _run(finalize_income_year, self.get_object(), request.user, confirm)
        return Response(self.get_serializer(year).data)

    @action(detail=True, methods=['post'])
    def revise(self, request, pk=None):
        original = self.get_object()
        if original.status != IncomeTaxYear.Status.FINALIZED:
            raise serializers.ValidationError({'status': 'Only a finalized year can be revised.'})
        revised = IncomeTaxYear.objects.create(
            workspace=original.workspace, year_end=original.year_end,
            basis=request.data.get('basis', original.basis), revision=original.revision + 1,
            supersedes=original, notes=request.data.get('notes', ''),
        )
        for line in original.stock_lines.all():
            StockValuationLine.objects.create(
                income_year=revised, category=line.category, description=line.description,
                source_type=line.source_type, source_id=line.source_id,
                quantity=line.quantity, unit_code=line.unit_code,
                original_cost=line.original_cost, method=line.method, value=line.value,
                currency_code=line.currency_code, evidence_url=line.evidence_url,
                assumptions=line.assumptions, derived=line.derived,
                provisional=line.provisional, created_by=request.user,
            )
        return Response(self.get_serializer(revised).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['get'])
    def export(self, request, pk=None):
        year = self.get_object()
        report = year.frozen_report if year.status == IncomeTaxYear.Status.FINALIZED else build_report(year)
        stream = StringIO(newline='')
        writer = csv.writer(stream)
        writer.writerow(('income_tax_year', report['version'], report['date_from'], report['date_to'], report['basis']))
        writer.writerow(())
        writer.writerow(('summary', 'amount', 'currency_code'))
        for name, value in report['totals'].items():
            writer.writerow((name, value, report['currency_code']))
        writer.writerow(())
        writer.writerow(('kind', 'date', 'source_type', 'source_id', 'reference', 'amount', 'currency_code'))
        for row in report['rows']:
            writer.writerow(tuple(row.get(key, '') for key in ('kind', 'date', 'source_type', 'source_id', 'reference', 'amount', 'currency_code')))
        response = HttpResponse(stream.getvalue(), content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="income-tax-{year.year_end}-r{year.revision}.csv"'
        return response


router = routers.DefaultRouter()
router.register('liabilities', LiabilityViewSet)
router.register('entries', EntryViewSet)
router.register('assets', TaxAssetViewSet)
router.register('depreciation-schedules', ScheduleViewSet)
router.register('income-years', IncomeYearViewSet)
