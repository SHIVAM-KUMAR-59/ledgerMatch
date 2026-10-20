import { useCallback, useEffect, useRef, useState } from 'react'

/**
 * Loads data with `load(signal)` on mount and whenever `reload()` is called.
 * Old data stays visible while a reload runs, and a response that arrives
 * after a newer request (or after unmount) is ignored.
 */
export function useResource(load) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const latest = useRef(0)
  const loadRef = useRef(load)

  // eslint-disable-next-line react-hooks/refs
  loadRef.current = load

  const refresh = useCallback(async (signal) => {
    const id = ++latest.current

    try {
      const result = await loadRef.current(signal)

      if (id === latest.current) {
        setData(result)
        setError(null)
      }
    } catch (err) {
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

  const reload = useCallback(() => {
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
