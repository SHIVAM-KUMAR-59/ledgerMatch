from django.urls import path

from .views import (
    LineItemListView,
    MatchApproveView,
    MatchRejectView,
    TransactionIgnoreView,
    TransactionImportView,
    TransactionListView,
)

urlpatterns = [
    path("line-items/", LineItemListView.as_view(), name="line-item-list"),
    path("transactions/", TransactionListView.as_view(), name="transaction-list"),
    path(
        "transactions/import/",
        TransactionImportView.as_view(),
        name="transaction-import",
    ),
    path(
        "transactions/<int:transaction_id>/ignore/",
        TransactionIgnoreView.as_view(),
        name="transaction-ignore",
    ),
    path(
        "matches/<int:match_id>/approve/",
        MatchApproveView.as_view(),
        name="match-approve",
    ),
    path(
        "matches/<int:match_id>/reject/",
        MatchRejectView.as_view(),
        name="match-reject",
    ),
]