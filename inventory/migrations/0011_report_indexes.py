"""Add access paths used by high-cardinality inventory reports."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [('inventory', '0010_stocktaketarget_review_revision_and_more')]

    operations = [
        migrations.AddIndex(
            model_name='stocklot',
            index=models.Index(
                fields=['workspace', 'expires_on'], name='stock_lot_expiry_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='stockmovement',
            index=models.Index(
                fields=['workspace', 'occurred_at'], name='stock_move_date_idx',
            ),
        ),
    ]
