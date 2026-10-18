export async function api<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const res = await fetch(`/api${path}`, {
    ...options,
    headers: {
      ...(options.body instanceof FormData
        ? {}
        : { "Content-Type": "application/json" }),
      ...options.headers,
    },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(
      typeof body.detail === "string"
        ? body.detail
        : `요청 실패 (${res.status}). 입력값과 서버 연결을 확인하세요.`,
    );
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}
export const post = <T>(path: string, data: unknown = {}) =>
  api<T>(path, { method: "POST", body: JSON.stringify(data) });
export const trackUrl = (pid: string, tid: string, kind = "audio") =>
  `/api/projects/${pid}/tracks/${tid}/${kind}`;
