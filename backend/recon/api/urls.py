from django.urls import path

from .views import LineItemListView

urlpatterns = [
    path("line-items/", LineItemListView.as_view(), name="line-item-list"),
]