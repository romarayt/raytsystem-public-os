let sessionPromise: Promise<string> | null = null;

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: Record<string, unknown>;

  constructor(
    status: number,
    code: string,
    message: string,
    details: Record<string, unknown> = {}
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

interface ErrorEnvelope {
  error?: { code?: string; message?: string; details?: Record<string, unknown> };
}

async function parseResponse<T>(response: Response): Promise<T> {
  const payload = (await response.json().catch(() => ({}))) as T & ErrorEnvelope;
  if (!response.ok) {
    throw new ApiError(
      response.status,
      payload.error?.code ?? "request_failed",
      payload.error?.message ?? "Локальная система не ответила на запрос.",
      payload.error?.details ?? {}
    );
  }
  return payload;
}

export async function ensureSession(): Promise<string> {
  sessionPromise ??= fetch("/api/v1/session", {
    credentials: "same-origin",
    headers: { Accept: "application/json" }
  })
    .then((response) => parseResponse<{ csrf_token: string }>(response))
    .then((payload) => payload.csrf_token)
    .catch((error: unknown) => {
      sessionPromise = null;
      throw error;
    });
  return sessionPromise;
}

export async function getJson<T>(path: string): Promise<T> {
  await ensureSession();
  const response = await fetch(path, {
    credentials: "same-origin",
    headers: { Accept: "application/json" }
  });
  return parseResponse<T>(response);
}

export async function postJson<T>(
  path: string,
  body: unknown,
  idempotencyKey: string = crypto.randomUUID()
): Promise<T> {
  const csrf = await ensureSession();
  const response = await fetch(path, {
    method: "POST",
    credentials: "same-origin",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      "Idempotency-Key": idempotencyKey,
      "X-CSRF-Token": csrf
    },
    body: JSON.stringify(body)
  });
  return parseResponse<T>(response);
}

export function shortId(value: string | null | undefined, head = 7, tail = 5): string {
  if (!value) return "—";
  return value.length <= head + tail + 1 ? value : `${value.slice(0, head)}…${value.slice(-tail)}`;
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return "Не указано";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("ru-RU", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit"
  }).format(date);
}
