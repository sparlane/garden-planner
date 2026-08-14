"""Read-oriented administration for customer sales."""

from django.contrib import admin

from .models import Customer, ReservationEvent, SalesOrder, SalesOrderAllocation, SalesOrderLine


admin.site.register(Customer)
admin.site.register(SalesOrder)
admin.site.register(SalesOrderLine)
admin.site.register(SalesOrderAllocation)
admin.site.register(ReservationEvent)
