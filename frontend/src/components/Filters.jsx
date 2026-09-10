// Filter controls, defined once. These were previously copy-pasted class
// strings on four pages, which is how the same control drifted into three
// slightly different sizes.
//
// Focus is handled globally by the :focus-visible ring in index.css, so the
// controls only describe their resting and hover states.

/** The one definition of a form control's resting look. Exported for controls
 *  that are not filters — the graph's depth selector, say — so the styling has
 *  a single source even where the component does not fit. */
export const FIELD =
  'rounded border border-edge bg-raised px-2 py-1 text-label text-ink transition-colors hover:border-edge-strong'

export function FilterBar({ children, summary }) {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-lg border border-edge bg-panel px-3 py-2">
      {children}
      {summary && <span className="ml-auto text-micro uppercase text-ink-faint">{summary}</span>}
    </div>
  )
}

export function SelectFilter({
  label,
  value,
  onChange,
  options,
  anyLabel = 'any',
  // Some enums are wire identifiers that read better as words in a dropdown
  // (`unseen_counterparty` → "unseen counterparty"). The value sent to the API
  // is unchanged; only the label is formatted.
  format = (o) => o,
}) {
  return (
    <label className="flex items-center gap-2 text-micro uppercase text-ink-faint">
      {label}
      <select value={value} onChange={(e) => onChange(e.target.value)} className={FIELD}>
        <option value="">{anyLabel}</option>
        {options.map((o) => (
          <option key={o} value={o}>
            {format(o)}
          </option>
        ))}
      </select>
    </label>
  )
}

export function TextFilter({ label, value, onChange, placeholder }) {
  return (
    <label className="flex items-center gap-2 text-micro uppercase text-ink-faint">
      {label}
      {/* Agent ids are identifiers, so the field they are typed into is
          monospace — it matches how they render in the rows below. */}
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className={`${FIELD} w-40 font-mono placeholder:font-sans placeholder:text-ink-faint`}
      />
    </label>
  )
}

export function ClearFilters({ show, onClear }) {
  if (!show) return null
  return (
    <button
      onClick={onClear}
      className="text-label text-accent transition-colors hover:underline"
    >
      clear filters
    </button>
  )
}
