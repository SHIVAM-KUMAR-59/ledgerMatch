export type Organization = 'northwind' | 'bluebird'
export type HttpMethod = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

export interface Match {
  id: number
  line_item: number | null
  investor_name?: string | null
  reference?: string | null
  reason?: string | null
  status: 'suggested' | 'proposed' | 'approved' | 'rejected' | string
}

export interface Transaction {
  id: number
  date: string
  description: string
  amount: string | number
  type: 'credit' | 'debit' | string
  reference: string
  status: string
  matches: Match[]
  reason?: string | null
}

export interface LineItem {
  id: number
  investor_name: string
  reference: string
  called_amount: string | number
  funded_amount: string | number
  remaining_amount: string | number
  status: string
  call_date?: string
}

export interface ImportRowError {
  line_number?: number
  line?: number
  message: string
}

export interface ImportSummary {
  imported_count?: number
  imported?: number
  duplicate_count?: number
  duplicates?: number
  errors?: ImportRowError[]
  [key: string]: unknown
}

interface RequestOptions {
  org: Organization
  method?: HttpMethod
  body?: BodyInit | null
  signal?: AbortSignal
}

async function request<T>(
  path: string,
  { org, method = 'GET', body, signal }: RequestOptions,
): Promise<T> {
  let response: Response

  try {
    response = await fetch(`/api${path}`, {
      method,
      headers: { 'X-Organization': org },
      body,
      signal,
    })
  } catch (error: unknown) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw error
    }

    throw new ApiError(0, 'Could not reach the server. Is the backend running?')
  }

  let data: unknown = null

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

  return data as T
}

export const listTransactions = (
  org: Organization,
  signal?: AbortSignal,
): Promise<Transaction[]> =>
  request<Transaction[]>('/transactions/', { org, signal })

export const listLineItems = (
  org: Organization,
  signal?: AbortSignal,
): Promise<LineItem[]> => request<LineItem[]>('/line-items/', { org, signal })

export function importCsv(
  org: Organization,
  file: File,
): Promise<ImportSummary> {
  const body = new FormData()
  body.append('file', file)

  return request<ImportSummary>('/transactions/import/', {
    org,
    method: 'POST',
    body,
  })
}

export const approveMatch = (
  org: Organization,
  matchId: number,
): Promise<Transaction> =>
  request<Transaction>(`/matches/${matchId}/approve/`, {
    org,
    method: 'POST',
  })

export const rejectMatch = (
  org: Organization,
  matchId: number,
): Promise<Transaction> =>
  request<Transaction>(`/matches/${matchId}/reject/`, {
    org,
    method: 'POST',
  })

export const ignoreTransaction = (
  org: Organization,
  transactionId: number,
): Promise<Transaction> =>
  request<Transaction>(`/transactions/${transactionId}/ignore/`, {
    org,
    method: 'POST',
  })
