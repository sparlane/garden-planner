"""Classify every sales line as a kind of supply, not only as a rate.

A GST return reports zero-rated supplies separately from exempt ones, and a
rate of zero cannot tell them apart. Existing lines are classified from what
their rate already establishes: a rate above zero is a standard-rated supply
by definition, while a rate of zero is genuinely unknown. Calling those
zero-rated would be a guess that lands a figure in a box of the return nobody
chose, so they become `unclassified` and are reported as a gap until somebody
says which they are.

The backfill runs between adding the column and adding the constraint that
requires it, because every existing row is blank until it has run.
"""

from django.db import migrations, models


def classify_existing_lines(apps, schema_editor):
    """Derive a treatment for every line recorded before the column existed."""
    del schema_editor
    sales_order_line = apps.get_model("sales", "SalesOrderLine")
    fulfillment_line = apps.get_model("sales", "FulfillmentLine")
    refund_line = apps.get_model("sales", "RefundLine")

    sales_order_line.objects.filter(tax_rate__gt=0).update(tax_treatment="standard")
    sales_order_line.objects.filter(tax_rate=0).update(tax_treatment="unclassified")

    # The posted records carry their own snapshot, so they are filled from the
    # line each one was posted against rather than re-derived from a rate they
    # do not store. Iterating keeps it readable; an update cannot span the join.
    treatments = dict(sales_order_line.objects.values_list("pk", "tax_treatment"))
    posted = list(
        fulfillment_line.objects.values_list("pk", "allocation__line_id"),
    )
    for treatment in set(treatments.values()):
        matching = [pk for pk, line_id in posted if treatments.get(line_id) == treatment]
        if matching:
            fulfillment_line.objects.filter(pk__in=matching).update(tax_treatment=treatment)
            refund_line.objects.filter(fulfillment_line__in=matching).update(
                tax_treatment=treatment,
            )


def clear_classifications(apps, schema_editor):
    """Reverse the backfill so the constraint can be dropped and re-applied."""
    del schema_editor
    for model in ("SalesOrderLine", "FulfillmentLine", "RefundLine"):
        apps.get_model("sales", model).objects.update(tax_treatment="")


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0011_report_indexes"),
        ("plants", "0003_maturity_basis"),
        ("sales", "0003_report_indexes"),
    ]

    operations = [
        migrations.AddField(
            model_name="fulfillmentline",
            name="tax_treatment",
            field=models.CharField(blank=True, default="", max_length=16),
        ),
        migrations.AddField(
            model_name="refundline",
            name="tax_treatment",
            field=models.CharField(blank=True, default="", max_length=16),
        ),
        migrations.AddField(
            model_name="salesorderline",
            name="tax_treatment",
            field=models.CharField(
                blank=True,
                choices=[
                    ("standard", "Standard-rated"),
                    ("zero_rated", "Zero-rated"),
                    ("exempt", "Exempt"),
                    ("out_of_scope", "Outside the scope of GST"),
                    ("unclassified", "Not yet classified"),
                ],
                default="",
                max_length=16,
            ),
        ),
        migrations.RunPython(classify_existing_lines, clear_classifications),
        migrations.AddConstraint(
            model_name="salesorderline",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("tax_rate__gt", 0), ("tax_treatment", "standard")),
                    models.Q(
                        ("tax_rate", 0),
                        (
                            "tax_treatment__in",
                            ("zero_rated", "exempt", "out_of_scope", "unclassified"),
                        ),
                    ),
                    _connector="OR",
                ),
                name="sales_line_tax_treatment_matches_rate",
            ),
        ),
    ]
