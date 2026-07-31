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
    console.error(`API request failed: ${options.method ?? "GET"} ${path}`, error);
    throw error;
  }
}
