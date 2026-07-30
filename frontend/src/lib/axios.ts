import axios from "axios";

import { clearSession, getAccessToken } from "./authStorage";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? "";

export const axiosInstance = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    "Content-Type": "application/json",
  },
});

// Attach the current access token per request. It has to be read at request time, not at
// module load, so a token stored by a login later in the session is actually used.
axiosInstance.interceptors.request.use((config) => {
  const token = getAccessToken();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

axiosInstance.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error?.response?.status === 401) {
      clearSession();
      window.location.href = "/login";
    }
    return Promise.reject(error);
  }
)
