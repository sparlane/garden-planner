"""Add date and operational-state access paths used by Nursery reports."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [('sales', '0002_fulfillmentnumbersequence_and_more')]

    operations = [
        migrations.AddIndex(
            model_name='salesorder',
            index=models.Index(
                fields=['workspace', 'status', 'requested_date'],
                name='sales_order_report_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='fulfillment',
            index=models.Index(
                fields=['workspace', 'fulfilled_at'], name='sales_fulfill_date_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='payment',
            index=models.Index(
                fields=['workspace', 'paid_on'], name='sales_payment_date_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='salesreturn',
            index=models.Index(
                fields=['workspace', 'returned_at'], name='sales_return_date_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='refund',
            index=models.Index(
                fields=['workspace', 'refunded_at'], name='sales_refund_date_idx',
            ),
        ),
    ]
