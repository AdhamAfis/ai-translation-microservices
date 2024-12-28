import { toast } from "sonner";

declare global {
  namespace NodeJS {
    interface ProcessEnv {
      NEXT_PUBLIC_API_URL: string;
    }
  }
}

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8080';

export type APIResponse<T> = {
  data?: T;
  error?: {
    status: number;
    detail: string;
  };
};

export type AuthResponse = {
  token?: string;
  user_id?: string;
};

export type HistoryItem = {
  id: string;
  timestamp: string;
  in_text: string;
  out_text: string;
  type: 'e2a-translation' | 'a2e-translation' | 'summarization';
  formality?: string;
};

export type HistoryResponse = {
  data: HistoryItem[];
};

export async function callAPI<T>(
  endpoint: string,
  options: RequestInit = {}
): Promise<APIResponse<T>> {
  try {
    const url = `${API_BASE_URL}${endpoint}`;
    const headers = {
      'Content-Type': 'application/json',
      ...options.headers,
    };

    const response = await fetch(url, {
      ...options,
      headers,
      credentials: 'include',
    });

    if (!response.ok) {
      const error = await response.json();
      return {
        error: {
          status: response.status,
          detail: error.message || 'An error occurred',
        },
      };
    }

    const data = await response.json();
    return { data };
  } catch (error) {
    console.error('API call failed:', error);
    toast.error('Failed to connect to the server');
    return {
      error: {
        status: 500,
        detail: 'Failed to connect to the server',
      },
    };
  }
}
