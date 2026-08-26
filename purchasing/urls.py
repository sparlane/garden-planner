"""URL routing for purchasing and accounts payable."""

from django.urls import include, path
from rest_framework import routers

from .rest import (
    BusinessExpenseViewSet,
    ExpenseCategoryViewSet,
    PurchaseOrderViewSet,
    PurchaseRequisitionViewSet,
    PurchasingSummaryView,
    SupplierInvoiceViewSet,
    SupplierPaymentViewSet,
)


router = routers.DefaultRouter()
router.register('requisitions', PurchaseRequisitionViewSet)
router.register('orders', PurchaseOrderViewSet)
router.register('expense-categories', ExpenseCategoryViewSet)
router.register('invoices', SupplierInvoiceViewSet)
router.register('payments', SupplierPaymentViewSet)
router.register('expenses', BusinessExpenseViewSet)

urlpatterns = [
    path('summary/', PurchasingSummaryView.as_view(), name='purchasing-summary'),
    path('', include(router.urls)),
]
