import { formatDate, formatMoney, STATUS_LABELS } from '../format'
import ErrorBox from './ErrorBox'

function isActionable(txn) {
  return txn.status === 'suggested' || txn.status === 'needs_review'
}

// Which matches are worth showing for a row: the proposals still open, or the
// one that was approved. Rejected matches stay in the data but not on screen.
function visibleMatches(txn) {
  if (txn.status === 'reconciled') {
    return txn.matches.filter((m) => m.status === 'approved')
  }
  if (isActionable(txn)) {
    return txn.matches.filter((m) => m.status === 'proposed')
  }
  return []
}

// Only shown once a row has actually been sent to Airtable (or failed to be),
// so nothing appears when the integration is switched off.
function SyncNote({ txn }) {
  if (txn.sync_status === 'synced') {
    return (
      <span className="sync" title={txn.airtable_record_id}>
        Airtable: synced
      </span>
    )
  }
  if (txn.sync_status === 'sync_failed') {
    return (
      <span className="sync failed" title={txn.sync_error}>
        Airtable: sync failed
      </span>
    )
  }
  return null
}

function TransactionRow({ txn, busy, actions }) {
  const matches = visibleMatches(txn)
  const several = matches.length > 1
  const actionable = isActionable(txn)

  const reasons = []
  if (txn.reason) reasons.push(txn.reason)
  for (const m of matches) {
    reasons.push(several ? `${m.line_item.reference}: ${m.reason}` : m.reason)
  }

  return (
    <tr>
      <td className="nowrap">{formatDate(txn.date)}</td>
      <td>
        {txn.description}
        <div className="muted small">{txn.bank_reference}</div>
      </td>
      <td className={`num ${Number(txn.amount) < 0 ? 'negative' : ''}`}>
        {formatMoney(txn.amount)}
      </td>
      <td>
        <span className={`badge ${txn.status}`}>{STATUS_LABELS[txn.status]}</span>
        <SyncNote txn={txn} />
      </td>
      <td>
        {matches.length === 0 ? (
          <span className="muted">—</span>
        ) : (
          <ul className="plain">
            {matches.map((m) => (
              <li key={m.id}>
                {m.line_item.investor_name}{' '}
                <span className="ref">{m.line_item.reference}</span>
              </li>
            ))}
          </ul>
        )}
      </td>
      <td className="reasons">
        {reasons.length === 0 ? (
          <span className="muted">—</span>
        ) : (
          reasons.map((text, i) => <p key={i}>{text}</p>)
        )}
      </td>
      <td>
        {!actionable ? (
          <span className="muted">—</span>
        ) : (
          <>
            {matches.map((m) => (
              <div className="action-group" key={m.id}>
                {several && <span className="ref">{m.line_item.reference}</span>}
                <button
                  type="button"
                  className="primary"
                  disabled={busy}
                  onClick={() => actions.approve(m.id)}
                >
                  Approve
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => actions.reject(m.id)}
                >
                  Reject
                </button>
              </div>
            ))}
            <div className="action-group">
              <button
                type="button"
                disabled={busy}
                onClick={() => actions.ignore(txn.id)}
              >
                Ignore
              </button>
            </div>
          </>
        )}
      </td>
    </tr>
  )
}

export default function TransactionTable({ resource, busy, actions }) {
  const { data, error, loading, reload } = resource

  return (
    <section className="card">
      <div className="card-head">
        <h2>Bank transactions</h2>
        <button type="button" disabled={busy} onClick={() => reload()}>
          Refresh
        </button>
      </div>

      {loading && <p className="state">Loading transactions…</p>}
      {error && (
        <ErrorBox
          message={`Could not load transactions: ${error.message}`}
          actionLabel="Retry"
          onAction={reload}
        />
      )}
      {data && data.length === 0 && (
        <p className="state">
          No transactions yet. Upload a bank statement CSV above to get started.
        </p>
      )}
      {data && data.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Date</th>
                <th>Description</th>
                <th className="num">Amount</th>
                <th>Status</th>
                <th>Suggested investor / reference</th>
                <th>Reason</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {data.map((txn) => (
                <TransactionRow key={txn.id} txn={txn} busy={busy} actions={actions} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}