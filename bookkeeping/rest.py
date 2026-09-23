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

from workspaces.conversion import ConversionMethod, QuoteDirection
from workspaces.currency import CurrencyInputSerializerMixin
from workspaces.scoping import CurrentWorkspaceSerializerMixin, CurrentWorkspaceViewSetMixin

from .conversion import SOURCES, record_conversion
from .models import BookkeepingEntry, CurrencyConversion, DepreciationSchedule, IncomeTaxYear, LegalHoldEvent, Liability, StockValuationLine, TaxAsset, TaxRetentionRecord
from .services import build_report, capture_inventory, finalize_income_year, reverse_entry, set_legal_hold


#: One exchange-rate record per row, in the order a reviewer reads them: what
#: was converted, at what, taken from where, and whether it still stands.
RATE_COLUMNS = (
    'source_type', 'source_id', 'source_currency_code', 'target_currency_code',
    'rate', 'quote_direction', 'method', 'rate_source', 'effective_date',
    'converted_on', 'supersedes', 'superseded', 'reason',
)

#: What a figure reads as when it could not be stated. Blank would be a zero to
#: anything reading the file with a spreadsheet, and a withheld total is the
#: opposite of a zero.
NOT_STATED = 'not stated'


def _stated_amount(value):
    """Render one exported value, saying so where a figure was withheld."""
    return NOT_STATED if value is None else value


def _report_conversions(workspace, report):
    """Return every rate ever recorded against a record this report used.

    Superseded rates are included. A corrected conversion is two facts -- what
    was filed and what replaced it -- and an export that showed only the second
    would be missing the one a reviewer is asking about.
    """
    wanted = {
        (row.get('rate_source_type', row['source_type']), str(row.get('rate_source_id', row['source_id'])))
        for row in report['rows'] + report['cash_reconciliation']
    }
    wanted |= {('stock_valuation_line', str(line['id'])) for line in report['stock_lines']}
    rows = CurrencyConversion.objects.filter(
        workspace=workspace, source_type__in={pair[0] for pair in wanted},
    ).select_related('supersedes')
    return [row for row in rows if (row.source_type, row.source_id) in wanted]


def _rate_row(conversion):
    """Render one exchange-rate record as the audit row it is."""
    return (
        conversion.source_type, conversion.source_id,
        conversion.source_currency_code, conversion.target_currency_code,
        f'{conversion.rate:f}', conversion.quote_direction, conversion.method,
        conversion.rate_source, conversion.effective_date.isoformat(),
        conversion.converted_on.isoformat(),
        conversion.supersedes_id or '',
        'yes' if hasattr(conversion, 'superseded_by') else 'no',
        conversion.reason,
    )


def _run(command, *args, **kwargs):
    try:
        return command(*args, **kwargs)
    except DjangoValidationError as exc:
        detail = exc.message_dict if hasattr(exc, 'message_dict') else exc.messages
        raise serializers.ValidationError(detail) from exc


class LiabilitySerializer(CurrencyInputSerializerMixin, CurrentWorkspaceSerializerMixin, serializers.ModelSerializer):
    class Meta:
        model = Liability
        fields = '__all__'
        read_only_fields = ['workspace', 'created']


class EntrySerializer(CurrencyInputSerializerMixin, CurrentWorkspaceSerializerMixin, serializers.ModelSerializer):
    class Meta:
        model = BookkeepingEntry
        fields = '__all__'
        read_only_fields = ['workspace', 'created_by', 'created', 'operation_key', 'reversal_of']


class TaxAssetSerializer(CurrencyInputSerializerMixin, CurrentWorkspaceSerializerMixin, serializers.ModelSerializer):
    class Meta:
        model = TaxAsset
        fields = '__all__'
        read_only_fields = ['workspace', 'created_by', 'created', 'updated']


class ScheduleSerializer(CurrentWorkspaceSerializerMixin, serializers.ModelSerializer):
    class Meta:
        model = DepreciationSchedule
        fields = '__all__'
        read_only_fields = ['workspace', 'created_by', 'created']


class StockLineSerializer(CurrencyInputSerializerMixin, serializers.ModelSerializer):
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


class LegalHoldEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = LegalHoldEvent
        fields = '__all__'
        read_only_fields = ['id', 'workspace', 'retention', 'active', 'reason', 'created_by', 'created']


class RetentionSerializer(serializers.ModelSerializer):
    hold_events = LegalHoldEventSerializer(many=True, read_only=True)

    class Meta:
        model = TaxRetentionRecord
        fields = '__all__'
        read_only_fields = [
            'id', 'workspace', 'source_type', 'source_id', 'income_year_end',
            'retain_until', 'legal_hold', 'reason', 'created_by', 'created',
            'hold_events',
        ]


class ConversionSerializer(serializers.ModelSerializer):
    class Meta:
        model = CurrencyConversion
        fields = '__all__'
        read_only_fields = [
            'id', 'workspace', 'source_currency_code', 'target_currency_code',
            'amounts', 'converted_on', 'created_by', 'created',
        ]


class ConversionRequestSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """What an operator types when converting one transaction.

    Only the rate and the way it is quoted are required. The currencies, the
    amounts and the transaction's own date are read off the record being
    converted, so a request cannot claim to convert an amount that is not
    there; and the method defaults to whatever the workspace converts by,
    because naming a different one is the thing the policy exists to refuse.
    """

    source_type = serializers.ChoiceField(
        choices=[(source.source_type, source.label) for source in SOURCES],
    )
    source_id = serializers.CharField(max_length=128)
    rate = serializers.DecimalField(max_digits=18, decimal_places=10)
    quote_direction = serializers.ChoiceField(choices=QuoteDirection.choices)
    method = serializers.ChoiceField(
        choices=ConversionMethod.choices, required=False,
    )
    rate_source = serializers.CharField(max_length=255)
    effective_date = serializers.DateField(required=False)
    supersedes = serializers.IntegerField(required=False)
    reason = serializers.CharField(required=False, allow_blank=True, default='')


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
        """Write the year as one file: totals, rows, and the rates behind them.

        The exchange-rate records are their own section rather than columns on
        the rows, because that is what they are -- one rate per transaction,
        recorded once and applied to every amount on it -- and because the
        superseded ones belong in the file too. A reviewer reading it years
        later can reproduce any converted figure from the row and the rate
        beside it without the application being available.
        """
        year = self.get_object()
        report = year.frozen_report if year.status == IncomeTaxYear.Status.FINALIZED else build_report(year)
        stream = StringIO(newline='')
        writer = csv.writer(stream)
        writer.writerow(('income_tax_year', report['version'], report['date_from'], report['date_to'], report['basis']))
        writer.writerow(())
        writer.writerow(('summary', 'amount', 'currency_code'))
        for name, value in report['totals'].items():
            writer.writerow((name, _stated_amount(value), report['currency_code']))
        writer.writerow(())
        writer.writerow((
            'kind', 'date', 'source_type', 'source_id', 'reference', 'amount',
            'currency_code', 'converted_amount', 'converted_currency_code',
        ))
        for row in report['rows']:
            writer.writerow(tuple(
                _stated_amount(row.get(key, ''))
                for key in (
                    'kind', 'date', 'source_type', 'source_id', 'reference',
                    'amount', 'currency_code', 'converted_amount',
                    'converted_currency_code',
                )
            ))
        writer.writerow(())
        writer.writerow(RATE_COLUMNS)
        for conversion in _report_conversions(year.workspace, report):
            writer.writerow(_rate_row(conversion))
        response = HttpResponse(stream.getvalue(), content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="income-tax-{year.year_end}-r{year.revision}.csv"'
        return response


class RetentionViewSet(CurrentWorkspaceViewSetMixin, viewsets.ReadOnlyModelViewSet):
    queryset = TaxRetentionRecord.objects.prefetch_related('hold_events')
    serializer_class = RetentionSerializer

    @action(detail=True, methods=['post'])
    def hold(self, request, pk=None):
        active = serializers.BooleanField().run_validation(request.data.get('active'))
        reason = serializers.CharField().run_validation(request.data.get('reason'))
        _run(set_legal_hold, self.get_object(), active, reason, request.user)
        return Response(self.get_serializer(self.get_object()).data)


class CurrencyConversionViewSet(CurrentWorkspaceViewSetMixin, viewsets.ReadOnlyModelViewSet):
    """Type a rate against one transaction, and read back every rate typed.

    The superseded conversions are listed beside the live ones on purpose: the
    question a reviewer asks about a corrected rate is what it used to be.
    `live=true` narrows the list to the rate each record is currently read at.
    """

    queryset = CurrencyConversion.objects.select_related('supersedes')
    serializer_class = ConversionSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        source_type = self.request.query_params.get('source_type')
        source_id = self.request.query_params.get('source_id')
        if source_type:
            queryset = queryset.filter(source_type=source_type)
        if source_id:
            queryset = queryset.filter(source_id=source_id)
        if self.request.query_params.get('live') == 'true':
            queryset = queryset.filter(superseded_by__isnull=True)
        return queryset

    def create(self, request, *args, **kwargs):
        serializer = ConversionRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = dict(serializer.validated_data)
        conversion = _run(
            record_conversion,
            self.get_current_workspace(),
            values.pop('source_type'),
            values.pop('source_id'),
            values,
            request.user,
        )
        return Response(
            self.get_serializer(conversion).data, status=status.HTTP_201_CREATED,
        )


router = routers.DefaultRouter()
router.register('liabilities', LiabilityViewSet)
router.register('entries', EntryViewSet)
router.register('assets', TaxAssetViewSet)
router.register('depreciation-schedules', ScheduleViewSet)
router.register('income-years', IncomeYearViewSet)
router.register('retention', RetentionViewSet)
router.register('currency-conversions', CurrencyConversionViewSet)
