import { formatMoney, STATUS_LABELS } from '../format'
import ErrorBox from './ErrorBox'

export default function LineItemPanel({ resource }) {
  const { data, error, loading, reload } = resource

  return (
    <section className="card">
      <div className="card-head">
        <h2>Capital call line items</h2>
      </div>

      {loading && <p className="state">Loading line items…</p>}
      {error && (
        <ErrorBox
          message={`Could not load line items: ${error.message}`}
          actionLabel="Retry"
          onAction={reload}
        />
      )}
      {data && data.length === 0 && (
        <p className="state">
          No line items for this organization. Run <span className="ref">python manage.py seed_demo</span>.
        </p>
      )}
      {data && data.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Investor</th>
                <th>Reference</th>
                <th>Call</th>
                <th className="num">Called</th>
                <th className="num">Funded</th>
                <th className="num">Remaining</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {data.map((item) => (
                <tr key={item.id}>
                  <td>{item.investor_name}</td>
                  <td>
                    <span className="ref">{item.reference}</span>
                  </td>
                  <td>#{item.call_number}</td>
                  <td className="num">{formatMoney(item.amount_called)}</td>
                  <td className="num">{formatMoney(item.amount_funded)}</td>
                  <td className="num">{formatMoney(item.remaining)}</td>
                  <td>
                    <span className={`badge ${item.status}`}>
                      {STATUS_LABELS[item.status]}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}