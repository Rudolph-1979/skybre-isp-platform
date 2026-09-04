/**
 * Turn whatever an API call rejected with into one sentence for a user.
 *
 * Lifted out of AccountantPage, which was the only page that had it, so
 * the ~15 mutations that catch nothing at all can share one
 * implementation instead of each inventing a slightly different one.
 *
 * Three shapes have to be handled, because DRF returns all three:
 *
 *   {"detail": "..."}            an APIException, a 403, a 409
 *   {"amount": ["..."]}          field-keyed validation errors, which is
 *                                what the payment and invoice serializers
 *                                now return, and what a naive
 *                                `data.detail` reader shows as nothing
 *   "<!DOCTYPE html>..."         an nginx or gunicorn 502/504, which is a
 *                                STRING -- and Object.values() on a string
 *                                spreads it into single characters, so the
 *                                old inline version of this rendered an
 *                                error banner containing exactly "<"
 */
export function apiErrorMessage(err: unknown, fallback: string): string {
  const data = (err as { response?: { data?: unknown } })?.response?.data;
  if (!data || typeof data !== "object") return fallback;

  const detail = (data as { detail?: unknown }).detail;
  if (typeof detail === "string") return detail;

  // Field-keyed: take the first message, prefixed with its field unless
  // the field is a non-field error bucket.
  for (const [field, value] of Object.entries(data as Record<string, unknown>)) {
    const first = Array.isArray(value) ? value[0] : value;
    if (typeof first !== "string") continue;
    return field === "non_field_errors" || field === "detail" ? first : `${field}: ${first}`;
  }
  return fallback;
}
