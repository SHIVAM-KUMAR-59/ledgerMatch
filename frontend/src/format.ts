const money = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
})

// The API sends amounts as decimal strings ("25000.00"). Display only.
export function formatMoney(value: string) {
  return money.format(Number(value))
}

// "2026-04-03" -> "03 Apr 2026". Built from parts so a time zone can't shift the day.
export function formatDate(iso: string) {
  const [year, month, day] = iso.split('-').map(Number)
  return new Date(year, month - 1, day).toLocaleDateString('en-GB', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
  })
}

export const STATUS_LABELS = {
  suggested: 'Suggested',
  needs_review: 'Needs review',
  reconciled: 'Reconciled',
  ignored: 'Ignored',
  open: 'Open',
  partially_funded: 'Partially funded',
  funded: 'Funded',
}