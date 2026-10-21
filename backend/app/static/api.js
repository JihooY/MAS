import { t } from './i18n.js';
export async function api(path, options = {}) {
  const response = await fetch(`/api${path}`, {
    ...options,
    headers: {
      ...(options.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
      ...options.headers,
    },
  });
  if (!response.ok) {
    if (response.status === 401) window.location.assign('/login');
    const body = await response.json().catch(() => ({}));
    throw new Error(typeof body.detail === 'string' ? t(body.detail) : t('요청 실패 ({status}). 입력값과 서버 연결을 확인하세요.',{status:response.status}));
  }
  return response.status === 204 ? null : response.json();
}
export const post = (path, body = {}) => api(path, { method: 'POST', body: JSON.stringify(body) });
export const trackUrl = (pid, tid) => `/api/projects/${pid}/tracks/${tid}/audio`;
export const fmt = value => {
  const n = Math.max(0, Number(value) || 0);
  return `${Math.floor(n / 60).toString().padStart(2, '0')}:${Math.floor(n % 60).toString().padStart(2, '0')}`;
};
export const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);
export const icon = (name, size = 17, extra = '') => `<svg class="icon ${extra}" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><use href="/static/icons.svg#${name}"></use></svg>`;
export const preference = {
  get(key, fallback) { try { return JSON.parse(localStorage.getItem(`mas-${key}`) ?? 'null') ?? fallback; } catch { return fallback; } },
  set(key, value) { try { localStorage.setItem(`mas-${key}`, JSON.stringify(value)); } catch { /* Browser storage can be disabled. */ } },
};
