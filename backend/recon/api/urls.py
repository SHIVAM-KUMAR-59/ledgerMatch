from django.urls import path

from .views import LineItemListView, TransactionImportView

urlpatterns = [
    path("line-items/", LineItemListView.as_view(), name="line-item-list"),
    path(
        "transactions/import/",
        TransactionImportView.as_view(),
        name="transaction-import",
    ),
]