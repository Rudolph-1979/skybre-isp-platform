import { FormField, inputClass } from "./Modal";
import type { Service } from "../types";

// Where a line is installed (Rudolph, 1 Oct: "some clients have one account
// but 3 different services at different addresses"). Shared by the New/Edit
// service forms on the Services page and on a client's own page, the same way
// ServiceShapingFields is.
//
// On a NEW service nothing is pre-ticked (use_customer_address === null): staff
// have to answer before the form will save, so the address can't be skipped
// past by default (Rudolph, 1 Oct). An existing service always has an answer.

export type ServiceAddressValues = {
  use_customer_address: boolean | null;
  site_address: string;
  site_complex_or_area: string;
  site_city: string;
  site_zip_code: string;
  // "lat, lng" as typed or pasted from Google Maps; parsed on save.
  site_coordinates: string;
};

// Accepts "-28.123456, 26.123456" (what Google Maps copies), with or
// without a space. Exported for the payload and the Maps link.
const COORDS_RE = /^\s*(-?\d{1,2}(?:\.\d+)?)\s*,\s*(-?\d{1,3}(?:\.\d+)?)\s*$/;
export const COORDS_PATTERN = COORDS_RE.source;

export function parseCoordinates(text: string): { lat: number; lng: number } | null {
  const m = COORDS_RE.exec(text || "");
  if (!m) return null;
  const lat = Number(m[1]);
  const lng = Number(m[2]);
  if (lat < -90 || lat > 90 || lng < -180 || lng > 180) return null;
  return { lat, lng };
}

export function mapsLink(lat: number | string, lng: number | string) {
  return `https://www.google.com/maps?q=${lat},${lng}`;
}

/** "lat, lng" text -> the two API fields (null when blank or invalid). */
export function coordinatesPayload(text: string): { lat: number | null; lng: number | null } {
  const c = parseCoordinates(text);
  return c ? { lat: Number(c.lat.toFixed(6)), lng: Number(c.lng.toFixed(6)) } : { lat: null, lng: null };
}

/** API's two decimal strings -> the "lat, lng" text the box shows. */
export function coordinatesText(lat: string | number | null | undefined, lng: string | number | null | undefined) {
  return lat != null && lng != null && lat !== "" && lng !== "" ? `${Number(lat)}, ${Number(lng)}` : "";
}

/** One box for GPS coordinates, pasted from Google Maps -- shared by the
 *  service address and the client's main address (farms have no street). */
export function CoordinatesField({
  value,
  onChange,
  label = "Coordinates (optional)",
}: {
  value: string;
  onChange: (value: string) => void;
  label?: string;
}) {
  const c = parseCoordinates(value);
  return (
    <FormField label={label}>
      <input
        className={inputClass}
        inputMode="decimal"
        placeholder="-28.123456, 26.123456"
        pattern={COORDS_PATTERN}
        title="Latitude, longitude -- e.g. -28.123456, 26.123456"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
      {c ? (
        <a
          href={mapsLink(c.lat, c.lng)}
          target="_blank"
          rel="noreferrer"
          className="mt-1 inline-block text-xs text-[var(--series-1)] hover:underline"
        >
          Open in Google Maps
        </a>
      ) : (
        <p className="mt-1 text-xs text-[var(--text-muted)]">
          {value.trim()
            ? "Use latitude, longitude -- e.g. -28.123456, 26.123456"
            : "Right-click the spot in Google Maps and click the numbers to copy them, then paste here."}
        </p>
      )}
    </FormField>
  );
}

export const emptyServiceAddressValues: ServiceAddressValues = {
  use_customer_address: null, site_address: "", site_complex_or_area: "", site_city: "", site_zip_code: "",
  site_coordinates: "",
};

export function serviceAddressValuesFrom(service: Service): ServiceAddressValues {
  return {
    use_customer_address: service.use_customer_address ?? true,
    site_address: service.site_address ?? "",
    site_complex_or_area: service.site_complex_or_area ?? "",
    site_city: service.site_city ?? "",
    site_zip_code: service.site_zip_code ?? "",
    site_coordinates: coordinatesText(service.site_latitude, service.site_longitude),
  };
}

export function serviceAddressPayload(values: ServiceAddressValues): Record<string, unknown> {
  const coords = parseCoordinates(values.site_coordinates);
  // null never reaches here from a form -- the radios are `required` -- but
  // a missing answer is the main address, same as the server's default.
  // ONLY the address fields. Spreading `values` here sent the WHOLE form
  // (it is the full service form, typed down to these keys), overwriting the
  // other payload helpers' cleaned values -- "" for blank dates and ids --
  // so every service save failed validation (Fanie van den Heever, 1 Oct).
  return {
    use_customer_address: values.use_customer_address ?? true,
    site_address: values.site_address,
    site_complex_or_area: values.site_complex_or_area,
    site_city: values.site_city,
    site_zip_code: values.site_zip_code,
    // Rounded to the 6 decimals the server stores (~10 cm). Blank -> null.
    site_latitude: coords ? Number(coords.lat.toFixed(6)) : null,
    site_longitude: coords ? Number(coords.lng.toFixed(6)) : null,
  };
}

export function ServiceAddressFields({
  values,
  onChange,
  mainAddress,
  otherServiceCount = 0,
}: {
  values: ServiceAddressValues;
  onChange: (patch: Partial<ServiceAddressValues>) => void;
  // The client's main address as one line, when known.
  mainAddress?: string;
  // How many OTHER services this client already has. When there are any,
  // the question is put the way it matters: is this one at the same place?
  otherServiceCount?: number;
}) {
  const unanswered = values.use_customer_address === null;
  const existing =
    otherServiceCount > 0
      ? `This client already has ${otherServiceCount === 1 ? "a service" : `${otherServiceCount} services`}` +
        (mainAddress ? ` (main address: ${mainAddress})` : "") +
        ". Is this one at the same address?"
      : null;
  // One name per mounted block so the browser treats the two radios as one
  // required group.
  const group = "service-address-choice";

  return (
    <div className="mt-4 border-t border-[var(--border-hairline)] pt-4">
      <p className="mb-1 text-sm font-semibold text-[var(--text-primary)]">Service address</p>
      <p className={`mb-2 text-xs ${unanswered ? "text-[var(--status-warning)]" : "text-[var(--text-muted)]"}`}>
        {existing ?? "Where is this line installed?"}
        {unanswered && " Required — the technician goes where this says."}
      </p>
      <label className="mb-1 flex items-center gap-2 text-sm">
        <input
          type="radio"
          name={group}
          required
          checked={values.use_customer_address === true}
          onChange={() => onChange({ use_customer_address: true })}
        />
        {existing ? "Yes — same as the client's main address" : "Same as the client's main address"}
      </label>
      {mainAddress && !existing && <p className="mb-2 ml-6 text-xs text-[var(--text-muted)]">{mainAddress}</p>}
      <label className="mb-3 flex items-center gap-2 text-sm">
        <input
          type="radio"
          name={group}
          required
          checked={values.use_customer_address === false}
          onChange={() => onChange({ use_customer_address: false })}
        />
        {existing ? "No — a different address" : "A separate address for this service"}
      </label>
      {values.use_customer_address === false && (
        <>
          <FormField label="Street address (number and name)">
            <input
              className={inputClass}
              // Not needed when coordinates are given -- a farm or plot
              // often has no street to navigate to.
              required={!values.site_coordinates.trim()}
              value={values.site_address}
              onChange={(e) => onChange({ site_address: e.target.value })}
            />
          </FormField>
          <FormField label="Complex or residential area">
            <input
              className={inputClass}
              placeholder="e.g. 9A Cabanas Flats"
              value={values.site_complex_or_area}
              onChange={(e) => onChange({ site_complex_or_area: e.target.value })}
            />
          </FormField>
          <div className="grid grid-cols-2 gap-3">
            <FormField label="City">
              <input className={inputClass} value={values.site_city} onChange={(e) => onChange({ site_city: e.target.value })} />
            </FormField>
            <FormField label="Zip code">
              <input
                className={inputClass}
                value={values.site_zip_code}
                onChange={(e) => onChange({ site_zip_code: e.target.value })}
              />
            </FormField>
          </div>
          <CoordinatesField
            value={values.site_coordinates}
            onChange={(site_coordinates) => onChange({ site_coordinates })}
          />
        </>
      )}
    </div>
  );
}
