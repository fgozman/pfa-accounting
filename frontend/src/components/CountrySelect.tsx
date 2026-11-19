import { PARTNER_COUNTRIES } from "../labels";

/** Native country dropdown; keep a stored legacy code selectable even if it is not in the list. */
export function CountrySelect({
  id,
  label,
  value,
  onChange,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (code: string) => void;
}) {
  const selected = PARTNER_COUNTRIES.find(({ code }) => code === value);

  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <select id={id} value={value} onChange={(event) => onChange(event.target.value)}>
        <option value="">— nespecificată —</option>
        {value && !selected ? <option value={value}>{value}</option> : null}
        {PARTNER_COUNTRIES.map(({ code, name }) => (
          <option key={code} value={code}>{name} ({code})</option>
        ))}
      </select>
    </div>
  );
}
