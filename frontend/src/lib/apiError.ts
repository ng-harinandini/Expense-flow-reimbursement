import axios from "axios";

/**
 * Pulls a human-readable message out of a failed request.
 *
 * FastAPI puts the message in `detail` — a string for our HTTPExceptions, but an
 * array of error objects for request-validation (422) failures. Both are handled
 * so a validation error never renders as "[object Object]".
 */
export function getErrorMessage(error: unknown, fallback: string): string {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail;

    if (typeof detail === "string" && detail.trim()) {
      return detail;
    }

    if (Array.isArray(detail)) {
      const messages = detail
        .map((item) => (typeof item?.msg === "string" ? item.msg : null))
        .filter((msg): msg is string => Boolean(msg));
      if (messages.length) {
        return messages.join(" ");
      }
    }

    // No usable body: distinguish "server never answered" from a bare status.
    if (!error.response) {
      return "Cannot reach the server. Check your connection and try again.";
    }
  }

  return fallback;
}
