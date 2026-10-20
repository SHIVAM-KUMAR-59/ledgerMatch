from rest_framework import serializers

from recon.models import BankTransaction, LineItem, TransactionMatch


class LineItemSerializer(serializers.ModelSerializer):
    remaining = serializers.DecimalField(
        max_digits=14, decimal_places=2, read_only=True
    )
    call_number = serializers.IntegerField(source="capital_call.number", read_only=True)
    call_date = serializers.DateField(source="capital_call.call_date", read_only=True)
    due_date = serializers.DateField(source="capital_call.due_date", read_only=True)

    class Meta:
        model = LineItem
        fields = [
            "id", "investor_name", "reference", "call_number", "call_date",
            "due_date", "amount_called", "amount_funded", "remaining", "status",
        ]


class MatchLineItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = LineItem
        fields = ["id", "reference", "investor_name"]


class TransactionMatchSerializer(serializers.ModelSerializer):
    line_item = MatchLineItemSerializer(read_only=True)

    class Meta:
        model = TransactionMatch
        fields = ["id", "status", "reason", "line_item"]


class BankTransactionSerializer(serializers.ModelSerializer):
    matches = TransactionMatchSerializer(many=True, read_only=True)

    class Meta:
        model = BankTransaction
        fields = [
            "id", "date", "description", "amount", "type", "bank_reference",
            "status", "reason", "created_at", "matches",
        ]