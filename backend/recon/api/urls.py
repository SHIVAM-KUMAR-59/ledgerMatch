from django.urls import path

from .views import LineItemListView, TransactionImportView, TransactionListView

urlpatterns = [
    path("line-items/", LineItemListView.as_view(), name="line-item-list"),
    path("transactions/", TransactionListView.as_view(), name="transaction-list"),
    path(
        "transactions/import/",
        TransactionImportView.as_view(),
        name="transaction-import",
    ),
]