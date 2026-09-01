"""Widen tray sales lines to any individually numbered stock.

A tray line was never tray-shaped except in its name and one validation
branch: it already allocated `InventoryUnit` rows, which is exactly how a
numbered pot is sold. Renaming the field and the choice lets pots reuse the
whole path instead of growing a second, near-identical line type beside it.

The rename and the data step are one migration because they describe one
change; splitting them would leave a deploy window where the column and the
values disagreed.
"""

from django.db import migrations, models
import django.db.models.deletion


def widen_tray_lines(apps, schema_editor):
    """Rename the shipped `tray` line type in place."""
    line_model = apps.get_model('sales', 'SalesOrderLine')
    line_model.objects.filter(line_type='tray').update(line_type='unit')


def narrow_unit_lines(apps, schema_editor):
    """Return unit lines to `tray`, which is all they could have been."""
    line_model = apps.get_model('sales', 'SalesOrderLine')
    line_model.objects.filter(line_type='unit').update(line_type='tray')


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0017_alter_stocktaketarget_target_type'),
        ('sales', '0005_payment_account_reference_refund_account_reference'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='salesorderline',
            name='sales_line_target_matches_type',
        ),
        migrations.RenameField(
            model_name='salesorderline',
            old_name='tray_item',
            new_name='item',
        ),
        migrations.AlterField(
            model_name='salesorderline',
            name='item',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='+',
                to='inventory.inventoryitem',
            ),
        ),
        migrations.AlterField(
            model_name='salesorderline',
            name='line_type',
            field=models.CharField(
                choices=[('seedling', 'Seedling'), ('unit', 'Individually numbered unit')],
                max_length=16,
            ),
        ),
        migrations.RunPython(widen_tray_lines, narrow_unit_lines),
        migrations.AddConstraint(
            model_name='salesorderline',
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ('item__isnull', True),
                        ('line_type', 'seedling'),
                        ('variety__isnull', False),
                    ),
                    models.Q(
                        ('item__isnull', False),
                        ('line_type', 'unit'),
                        ('variety__isnull', True),
                    ),
                    _connector='OR',
                ),
                name='sales_line_target_matches_type',
            ),
        ),
    ]
