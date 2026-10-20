"""Pure matching rules for bank credits.

No Django imports and no database access: callers pass in snapshots of the
eligible-line-item data and get back a decision. Persisting that decision is
the import service's job.
"""

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

# Same string values as the TextChoices on the models, kept here so this
# module stays free of Django.
SUGGESTED = "suggested"
NEEDS_REVIEW = "needs_review"

_NAME_SUFFIXES = {"LLC", "LP", "LTD", "INC"}
_NON_ALNUM = re.compile(r"[^A-Z0-9]+")


def normalise_reference(value: str) -> str:
    """'cc-1003' -> 'CC1003': uppercase, letters and digits only."""
    return _NON_ALNUM.sub("", value.upper())


def normalise_text(value: str) -> str:
    """Uppercase, non-alphanumerics become single spaces, ends trimmed."""
    return _NON_ALNUM.sub(" ", value.upper()).strip()


def normalise_name(value: str) -> str:
    """Like normalise_text, then drop one trailing LLC/LP/LTD/INC (TRUST stays)."""
    words = normalise_text(value).split()
    if words and words[-1] in _NAME_SUFFIXES:
        words.pop()
    return " ".join(words)


def contains_words(haystack: str, needle: str) -> bool:
    """True if `needle` appears in `haystack` as whole words (both pre-normalised)."""
    return bool(needle) and f" {needle} " in f" {haystack} "


def _money(value: Decimal) -> str:
    return f"{value:,.2f}"


@dataclass(frozen=True)
class Candidate:
    """Snapshot of a line item, with its normalised forms computed once."""

    id: int
    reference: str
    investor_name: str
    remaining: Decimal
    call_date: date
    norm_reference: str
    norm_name: str

    @classmethod
    def build(cls, *, id, reference, investor_name, remaining, call_date):
        return cls(
            id=id,
            reference=reference,
            investor_name=investor_name,
            remaining=remaining,
            call_date=call_date,
            norm_reference=normalise_reference(reference),
            norm_name=normalise_name(investor_name),
        )


@dataclass(frozen=True)
class ProposedMatch:
    line_item_id: int
    reason: str


@dataclass(frozen=True)
class MatchDecision:
    status: str  # SUGGESTED or NEEDS_REVIEW
    reason: str  # transaction-level reason; empty for SUGGESTED (reason is on the match)
    matches: tuple[ProposedMatch, ...]


def decide(
    *,
    txn_date: date,
    amount: Decimal,
    description: str,
    bank_reference: str,
    candidates: list[Candidate],
) -> MatchDecision:
    eligible = [
        c for c in candidates if c.remaining > 0 and c.call_date <= txn_date
    ]

    haystacks = (normalise_reference(description), normalise_reference(bank_reference))
    by_reference = [
        c
        for c in eligible
        if c.norm_reference and any(c.norm_reference in h for h in haystacks)
    ]
    if by_reference:
        return _decide_by_reference(by_reference, amount)

    return _decide_by_name(eligible, amount, normalise_text(description))


def _decide_by_reference(found: list[Candidate], amount: Decimal) -> MatchDecision:
    if len(found) > 1:
        refs = ", ".join(c.reference for c in found)
        return MatchDecision(
            NEEDS_REVIEW,
            f"Several line items referenced in this payment: {refs}.",
            tuple(
                ProposedMatch(
                    c.id,
                    f"Reference {c.reference} found among several references in this payment.",
                )
                for c in found
            ),
        )

    item = found[0]
    if amount > item.remaining:
        return MatchDecision(
            NEEDS_REVIEW,
            f"Reference {item.reference} found, but amount {_money(amount)} exceeds "
            f"remaining {_money(item.remaining)} (overpayment).",
            (),
        )

    if amount == item.remaining:
        detail = f"amount equals remaining {_money(item.remaining)}"
    else:
        detail = f"partial payment {_money(amount)} of remaining {_money(item.remaining)}"
    return MatchDecision(
        SUGGESTED, "", (ProposedMatch(item.id, f"Reference {item.reference} found; {detail}."),)
    )


def _decide_by_name(
    eligible: list[Candidate], amount: Decimal, description: str
) -> MatchDecision:
    hits = [
        c
        for c in eligible
        if c.remaining == amount and contains_words(description, c.norm_name)
    ]

    def match_for(c: Candidate) -> ProposedMatch:
        return ProposedMatch(
            c.id,
            f"Investor name {c.investor_name} found; amount equals remaining "
            f"{_money(c.remaining)}.",
        )

    if len(hits) == 1:
        return MatchDecision(SUGGESTED, "", (match_for(hits[0]),))
    if len(hits) > 1:
        refs = ", ".join(c.reference for c in hits)
        return MatchDecision(
            NEEDS_REVIEW,
            f"Several line items match investor name and amount: {refs}.",
            tuple(match_for(c) for c in hits),
        )
    return MatchDecision(
        NEEDS_REVIEW,
        "No reference found and no line item matches both investor name and amount.",
        (),
    )