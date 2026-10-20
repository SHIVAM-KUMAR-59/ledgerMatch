import { useRef, useState } from 'react'
import { STATUS_LABELS } from '../format'
import ErrorBox from './ErrorBox'

function RowList({ title, items, render }) {
  if (items.length === 0) return null
  return (
    <div>
      <h3>{title}</h3>
      <ul className="row-list">{items.map(render)}</ul>
    </div>
  )
}

// Nothing is shown when the Airtable integration is switched off.
function AirtableLine({ airtable }) {
  if (!airtable?.enabled) return null
  const parts = [`${airtable.synced} synced`]
  if (airtable.failed) parts.push(`${airtable.failed} failed`)
  if (airtable.remaining) parts.push(`${airtable.remaining} waiting`)
  const problem = airtable.failed > 0 || Boolean(airtable.error)
  return (
    <p className={problem ? 'airtable-line failed' : 'muted'}>
      Airtable: {parts.join(', ')}
      {airtable.error ? ` — ${airtable.error}` : ''}
    </p>
  )
}

export default function UploadPanel({ busy, onUpload }) {
  const [file, setFile] = useState(null)
  const [uploading, setUploading] = useState(false)
  const [summary, setSummary] = useState(null)
  const [error, setError] = useState(null)
  const inputRef = useRef(null)

  async function submit(event) {
    event.preventDefault()
    if (!file) return
    setUploading(true)
    setError(null)
    setSummary(null)
    try {
      const result = await onUpload(file)
      if (result) {
        setSummary(result)
        setFile(null)
        if (inputRef.current) inputRef.current.value = ''
      }
    } catch (err) {
      setError(err.message)
    } finally {
      setUploading(false)
    }
  }

  return (
    <section className="card">
      <div className="card-head">
        <h2>Import bank statement</h2>
      </div>
      <form className="upload-form" onSubmit={submit}>
        <label htmlFor="csv-file" className="muted">
          CSV file (date, description, amount, type, reference)
        </label>
        <input
          id="csv-file"
          ref={inputRef}
          type="file"
          accept=".csv,text/csv"
          disabled={busy}
          onChange={(event) => setFile(event.target.files[0] ?? null)}
        />
        <button type="submit" className="primary" disabled={!file || busy}>
          {uploading ? 'Importing…' : 'Import CSV'}
        </button>
      </form>

      {error && (
        <div style={{ marginTop: 12 }}>
          <ErrorBox message={error} />
        </div>
      )}

      {summary && (
        <div className="summary">
          <p>
            <strong>{summary.imported}</strong> imported ·{' '}
            <strong>{summary.duplicates}</strong> duplicates skipped ·{' '}
            <strong>{summary.errors.length}</strong> rows with errors
          </p>
          <p className="muted">
            Imported rows:{' '}
            {Object.entries(summary.by_status)
              .map(([status, count]) => `${count} ${STATUS_LABELS[status].toLowerCase()}`)
              .join(', ')}
          </p>
          <AirtableLine airtable={summary.airtable} />
          <RowList
            title="Rows with errors (skipped)"
            items={summary.errors}
            render={(e) => (
              <li key={e.line}>
                <strong>Line {e.line}:</strong> {e.message}
              </li>
            )}
          />
          <RowList
            title="Duplicates (skipped)"
            items={summary.duplicate_rows}
            render={(d) => (
              <li key={d.line}>
                <strong>Line {d.line}:</strong> bank reference{' '}
                <span className="ref">{d.bank_reference}</span> was already imported
              </li>
            )}
          />
        </div>
      )}
    </section>
  )
}