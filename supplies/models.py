from django.db import models


class Supplier(models.Model):
    """
    A seed supplier
    """
    name = models.CharField(max_length=1024)
    website = models.CharField(max_length=1024, blank=True, null=True)
    notes = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.name
