import { useRef, useState } from 'react'
import {
  approveMatch,
  ignoreTransaction,
  importCsv,
  listLineItems,
  listTransactions,
  rejectMatch,
} from './api'
import ErrorBox from './components/ErrorBox'
import LineItemPanel from './components/LineItemPanel'
import TransactionTable from './components/TransactionTable'
import UploadPanel from './components/UploadPanel'
import { useResource } from './hooks/useResource'

export default function Dashboard({ org }) {
  const transactions = useResource((signal) => listTransactions(org, signal))
  const lineItems = useResource((signal) => listLineItems(org, signal))
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState(null)
  const busyRef = useRef(false) // blocks a second request even within the same tick

  // Runs one request at a time, then refreshes both lists whatever the outcome.
  // Errors are rethrown for the caller to display.
  async function runExclusive(task) {
    if (busyRef.current) return undefined
    busyRef.current = true
    setBusy(true)
    try {
      return await task()
    } finally {
      await Promise.all([transactions.reload(), lineItems.reload()])
      busyRef.current = false
      setBusy(false)
    }
  }

  async function runAction(task) {
    setActionError(null)
    try {
      await runExclusive(task)
    } catch (err) {
      setActionError(err.message)
    }
  }

  const actions = {
    approve: (matchId) => runAction(() => approveMatch(org, matchId)),
    reject: (matchId) => runAction(() => rejectMatch(org, matchId)),
    ignore: (transactionId) =>
      runAction(() => ignoreTransaction(org, transactionId)),
  }

  return (
    <>
      <UploadPanel
        busy={busy}
        onUpload={(file) => runExclusive(() => importCsv(org, file))}
      />
      {actionError && (
        <ErrorBox
          message={actionError}
          actionLabel="Dismiss"
          onAction={() => setActionError(null)}
        />
      )}
      <TransactionTable resource={transactions} busy={busy} actions={actions} />
      <LineItemPanel resource={lineItems} />
    </>
  )
}