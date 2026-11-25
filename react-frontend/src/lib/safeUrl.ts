/**
 * True for an absolute https URL. Used for links the API returns (short-lived
 * presigned document links) before they are put in an href or an object's data
 * attribute: javascript:, data:, blob:, http: and relative values are refused.
 */
export function isSafeHttpUrl(url: unknown): url is string {
  if (typeof url !== 'string' || url.length === 0 || url.length > 4000) return false;
  try {
    return new URL(url).protocol === 'https:';
  } catch {
    return false;
  }
}
