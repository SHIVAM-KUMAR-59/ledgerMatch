import { useCallback, useEffect, useRef, useState } from 'react'

type ResourceLoader<T> = (signal?: AbortSignal) => Promise<T>

interface UseResourceResult<T> {
  data: T | null
  error: Error | null
  loading: boolean
  reload: () => Promise<void>
}

/**
 * Loads data with `load(signal)` on mount and whenever `reload()` is called.
 * Old data stays visible while a reload runs, and a response that arrives
 * after a newer request (or after unmount) is ignored.
 */
export function useResource<T>(load: ResourceLoader<T>): UseResourceResult<T> {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<Error | null>(null)
  const latest = useRef(0)
  const loadRef = useRef(load)

  loadRef.current = load

  const refresh = useCallback(async (signal?: AbortSignal): Promise<void> => {
    const id = ++latest.current

    try {
      const result = await loadRef.current(signal)

      if (id === latest.current) {
        setData(result)
        setError(null)
      }
    } catch (err: unknown) {
      if (err instanceof Error && err.name === 'AbortError') {
        return
      }

      if (id === latest.current) {
        setError(
          err instanceof Error
            ? err
            : new Error('An unexpected error occurred.'),
        )
      }
    }
  }, [])

  useEffect(() => {
    const controller = new AbortController()

    void refresh(controller.signal)

    return () => {
      controller.abort()
      latest.current += 1
    }
  }, [refresh])

  const reload = useCallback((): Promise<void> => {
    setError(null)
    return refresh()
  }, [refresh])

  return {
    data,
    error,
    loading: data === null && error === null,
    reload,
  }
}
