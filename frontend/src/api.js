export class ApiError extends Error {

  constructor(status, message) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

async function request(
  path,
  { org, method = 'GET', body, signal },
) {
  let response

  try {
    response = await fetch(`/api${path}`, {
      method,
      headers: { 'X-Organization': org },
      body,
      signal,
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw error
    }

    throw new ApiError(0, 'Could not reach the server. Is the backend running?')
  }

  let data = null

  try {
    data = await response.json()
  } catch {
    // The response may have an empty body or non-JSON content.
  }

  if (!response.ok) {
    const message =
      typeof data === 'object' &&
      data !== null &&
      'detail' in data &&
      typeof data.detail === 'string'
        ? data.detail
        : `Request failed (${response.status}).`

    throw new ApiError(response.status, message)
  }

  return data
}

export const listTransactions = (
  org,
  signal,
) =>
  request('/transactions/', { org, signal })

export const listLineItems = (
  org,
  signal,
) => request('/line-items/', { org, signal })

export function importCsv(
  org,
  file,
) {
  const body = new FormData()
  body.append('file', file)

  return request('/transactions/import/', {
    org,
    method: 'POST',
    body,
  })
}

export const approveMatch = (
  org,
  matchId,
) =>
  request(`/matches/${matchId}/approve/`, {
    org,
    method: 'POST',
  })

export const rejectMatch = (
  org,
  matchId,
) =>
  request(`/matches/${matchId}/reject/`, {
    org,
    method: 'POST',
  })

export const ignoreTransaction = (
  org,
  transactionId,
) =>
  request(`/transactions/${transactionId}/ignore/`, {
    org,
    method: 'POST',
  })
