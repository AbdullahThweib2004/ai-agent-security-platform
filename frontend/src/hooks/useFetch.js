import { useCallback, useEffect, useState } from 'react'

/** Run an async loader, tracking loading and error state. */
export function useFetch(loader, deps = []) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)

  // eslint-disable-next-line react-hooks/exhaustive-deps
  const run = useCallback(loader, deps)

  const reload = useCallback(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    run()
      .then((d) => !cancelled && setData(d))
      .catch((e) => !cancelled && setError(e.message || String(e)))
      .finally(() => !cancelled && setLoading(false))
    return () => {
      cancelled = true
    }
  }, [run])

  useEffect(() => reload(), [reload])

  return { data, error, loading, reload }
}
