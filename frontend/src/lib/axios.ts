import axios from "axios";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? "";
const ACCESS_TOKEN = process.env.NEXT_PUBLIC_ACCESS_TOKEN ?? "";

export const axiosInstance = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    "Content-Type": "application/json",
    Authorization: `Bearer ${ACCESS_TOKEN}`,
  },
});
