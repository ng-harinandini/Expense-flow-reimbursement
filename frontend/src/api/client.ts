import { axiosInstance } from "@/lib/axios";

export async function apiRequest<T>(
  path: string,
  options: { method?: string; body?: string; headers?: Record<string, string> } = {}
): Promise<T> {
  try {
    const response = await axiosInstance.request<T>({
      url: path,
      method: options.method ?? "GET",
      data: options.body,
      headers: options.headers,
    });
    return response.data;
  } catch (error) {
    console.warn(`API request failed: ${options.method ?? "GET"} ${path}`, error);
    throw error;
  }
}

/**
 * Multipart variant of {@link apiRequest} for file uploads.
 *
 * `Content-Type` is explicitly undefined so axios lets the browser set it — the
 * multipart boundary is generated per request and cannot come from the
 * instance-level JSON default, which would otherwise make the body unparseable
 * server-side.
 */
export async function apiUpload<T>(
  path: string,
  formData: FormData,
  options: { method?: string; signal?: AbortSignal } = {}
): Promise<T> {
  try {
    const response = await axiosInstance.request<T>({
      url: path,
      method: options.method ?? "POST",
      data: formData,
      headers: { "Content-Type": undefined },
      signal: options.signal,
    });
    return response.data;
  } catch (error) {
    console.warn(`API upload failed: ${options.method ?? "POST"} ${path}`, error);
    throw error;
  }
}
