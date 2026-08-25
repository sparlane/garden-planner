from decimal import Decimal, ROUND_HALF_UP

import django.core.validators
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import workspaces.models


MONEY = Decimal('0.0001')


def preserve_receipt_tax(apps, schema_editor):
    StockReceiptLine = apps.get_model('inventory', 'StockReceiptLine')
    for line in StockReceiptLine.objects.select_related('receipt__supplier').iterator():
        receipt = line.receipt
        ex_tax = Decimal(line.line_cost_ex_tax).quantize(MONEY)
        tax = (
            ex_tax * Decimal(receipt.tax_rate) / Decimal('100')
        ).quantize(MONEY, rounding=ROUND_HALF_UP)
        recoverable = tax if receipt.tax_recoverable else Decimal('0.0000')
        line.supplier_cost_incl_tax = ex_tax + tax
        line.tax_treatment = 'standard' if tax > 0 else 'unknown'
        line.tax_rate = receipt.tax_rate if tax > 0 else Decimal('0')
        line.input_tax_source = 'supplier' if tax > 0 else 'none'
        line.input_tax_amount = tax
        line.claim_input_tax = recoverable > 0
        line.claimable_percentage = Decimal('100') if recoverable > 0 else Decimal('0')
        line.recoverable_input_tax = recoverable
        line.non_recoverable_tax = tax - recoverable
        line.acquisition_amount = ex_tax + tax - recoverable
        line.legacy_tax_classification = True
        line.save(update_fields=[
            'supplier_cost_incl_tax', 'tax_treatment', 'tax_rate',
            'input_tax_source', 'input_tax_amount', 'claim_input_tax',
            'claimable_percentage', 'recoverable_input_tax',
            'non_recoverable_tax', 'acquisition_amount',
            'legacy_tax_classification',
        ])
        type(receipt).objects.filter(pk=receipt.pk).update(
            source_document_number=receipt.supplier_reference,
            supplier_name_snapshot=receipt.supplier.name,
            supplier_address_snapshot=receipt.supplier.address,
            supplier_gst_status=receipt.supplier.gst_status,
            supplier_gst_number=receipt.supplier.gst_number,
        )


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('supplies', '0004_supplier_tax_identity'),
        ('inventory', '0012_stockreceipt_settled_on'),
    ]

    operations = [
        migrations.AddField(
            model_name='stockreceipt',
            name='evidence_reference',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.AddField(
            model_name='stockreceipt',
            name='evidence_url',
            field=models.URLField(blank=True, default='', max_length=2048),
        ),
        migrations.AddField(
            model_name='stockreceipt',
            name='invoice_date',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='stockreceipt',
            name='source_document_number',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.AddField(
            model_name='stockreceipt',
            name='source_document_type',
            field=models.CharField(
                choices=[
                    ('none', 'No source document recorded'),
                    ('taxable_supply', 'Taxable supply information'),
                    ('invoice', 'Invoice'),
                    ('receipt', 'Receipt'),
                    ('buyer_created', 'Buyer-created taxable supply information'),
                    ('customs_entry', 'Customs entry or statement'),
                    ('contract', 'Contract or supplier agreement'),
                    ('bank_record', 'Bank or payment record'),
                    ('other', 'Other record'),
                ],
                default='none',
                max_length=24,
            ),
        ),
        migrations.AddField(
            model_name='stockreceipt',
            name='supplier_address_snapshot',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='stockreceipt',
            name='supplier_gst_number',
            field=models.CharField(blank=True, default='', max_length=16),
        ),
        migrations.AddField(
            model_name='stockreceipt',
            name='supplier_gst_status',
            field=models.CharField(
                choices=[
                    ('registered', 'GST registered'),
                    ('unregistered', 'Not GST registered'),
                    ('unknown', 'Unknown'),
                ],
                default='unknown',
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name='stockreceipt',
            name='supplier_name_snapshot',
            field=models.CharField(blank=True, default='', max_length=1024),
        ),
        migrations.AddField(
            model_name='stockreceiptline',
            name='acquisition_amount',
            field=models.DecimalField(decimal_places=4, default=Decimal('0'), editable=False, max_digits=18, validators=[django.core.validators.MinValueValidator(Decimal('0'))]),
        ),
        migrations.AddField(
            model_name='stockreceiptline',
            name='apportionment_basis',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='stockreceiptline',
            name='claim_input_tax',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='stockreceiptline',
            name='claimable_percentage',
            field=models.DecimalField(decimal_places=4, default=Decimal('0'), max_digits=7, validators=[django.core.validators.MinValueValidator(Decimal('0')), django.core.validators.MaxValueValidator(Decimal('100'))]),
        ),
        migrations.AddField(
            model_name='stockreceiptline',
            name='input_tax_amount',
            field=models.DecimalField(decimal_places=4, default=Decimal('0'), max_digits=18, validators=[django.core.validators.MinValueValidator(Decimal('0'))]),
        ),
        migrations.AddField(
            model_name='stockreceiptline',
            name='input_tax_source',
            field=models.CharField(choices=[('none', 'No input tax'), ('supplier', 'GST charged by supplier'), ('customs', 'GST levied by Customs'), ('second_hand', 'Second-hand-goods deduction')], default='none', max_length=16),
        ),
        migrations.AddField(
            model_name='stockreceiptline',
            name='legacy_tax_classification',
            field=models.BooleanField(default=False, editable=False),
        ),
        migrations.AddField(
            model_name='stockreceiptline',
            name='non_recoverable_tax',
            field=models.DecimalField(decimal_places=4, default=Decimal('0'), editable=False, max_digits=18, validators=[django.core.validators.MinValueValidator(Decimal('0'))]),
        ),
        migrations.AddField(
            model_name='stockreceiptline',
            name='recoverable_input_tax',
            field=models.DecimalField(decimal_places=4, default=Decimal('0'), editable=False, max_digits=18, validators=[django.core.validators.MinValueValidator(Decimal('0'))]),
        ),
        migrations.AddField(
            model_name='stockreceiptline',
            name='supplier_cost_incl_tax',
            field=models.DecimalField(decimal_places=4, default=Decimal('0'), max_digits=18, validators=[django.core.validators.MinValueValidator(Decimal('0'))]),
        ),
        migrations.AddField(
            model_name='stockreceiptline',
            name='tax_rate',
            field=models.DecimalField(decimal_places=4, default=Decimal('0'), max_digits=7, validators=[django.core.validators.MinValueValidator(Decimal('0')), django.core.validators.MaxValueValidator(Decimal('100'))]),
        ),
        migrations.AddField(
            model_name='stockreceiptline',
            name='tax_treatment',
            field=models.CharField(choices=[('standard', 'Standard-rated'), ('zero_rated', 'Zero-rated'), ('exempt', 'Exempt'), ('out_of_scope', 'Outside the scope of GST'), ('unknown', 'Unknown')], default='unknown', max_length=16),
        ),
        migrations.CreateModel(
            name='InputTaxAdjustment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('adjustment_date', models.DateField()),
                ('previous_claimable_percentage', models.DecimalField(decimal_places=4, max_digits=7, validators=[django.core.validators.MinValueValidator(Decimal('0')), django.core.validators.MaxValueValidator(Decimal('100'))])),
                ('revised_claimable_percentage', models.DecimalField(decimal_places=4, max_digits=7, validators=[django.core.validators.MinValueValidator(Decimal('0')), django.core.validators.MaxValueValidator(Decimal('100'))])),
                ('tax_adjustment', models.DecimalField(decimal_places=4, help_text='Positive increases the deduction; negative reduces it.', max_digits=18)),
                ('apportionment_basis', models.TextField()),
                ('reason', models.TextField()),
                ('evidence_reference', models.CharField(blank=True, default='', max_length=255)),
                ('evidence_url', models.URLField(blank=True, default='', max_length=2048)),
                ('created', models.DateTimeField(auto_now_add=True)),
                ('created_by', models.ForeignKey(editable=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('receipt_line', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='input_tax_adjustments', to='inventory.stockreceiptline')),
                ('workspace', models.ForeignKey(default=workspaces.models.get_default_workspace_id, editable=False, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='workspaces.workspace')),
            ],
            options={'ordering': ['adjustment_date', 'pk']},
        ),
        migrations.RunPython(preserve_receipt_tax, migrations.RunPython.noop),
    ]
