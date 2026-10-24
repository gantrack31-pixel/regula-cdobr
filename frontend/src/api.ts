export class ApiError extends Error { status: number; constructor(message: string, status: number) { super(message); this.status = status; } }
export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json');
  let response: Response;
  try { response = await fetch(`/api${path}`, { ...options, headers, credentials: 'same-origin' }); }
  catch { throw new ApiError('Не удалось связаться с сервером. Проверьте соединение и повторите попытку.', 0); }
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    const detail = payload?.detail;
    const message = typeof detail === 'string' ? detail : Array.isArray(detail) ? detail.map((item: {msg?: string}) => item.msg).filter(Boolean).join('; ') : `Запрос не выполнен (${response.status}). Попробуйте ещё раз.`;
    if (response.status === 401 && path !== '/auth/me' && path !== '/auth/login') window.dispatchEvent(new Event('regula:unauthorized'));
    throw new ApiError(message, response.status);
  }
  return response.status === 204 ? undefined as T : response.json();
}
export const post = <T,>(path: string, body: unknown = {}) => api<T>(path, { method: 'POST', body: JSON.stringify(body) });
export const errorMessage = (error: unknown) => error instanceof Error ? error.message : 'Что-то пошло не так. Повторите попытку.';
export const formatDate = (date?: string) => {
  if (!date) return '—';
  const parsed = new Date(date.endsWith('Z') || /[+-]\d\d:\d\d$/.test(date) || /^\d{4}-\d{2}-\d{2}$/.test(date) ? date : `${date}Z`);
  return Number.isNaN(parsed.getTime()) ? 'Дата не указана' : new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }).format(parsed);
};
export const sourceText = (source: unknown): string => typeof source === 'string' ? source : source && typeof source === 'object' ? Object.entries(source).filter(([key]) => !['id', 'document_id', 'version_id', 'source_url'].includes(key)).map(([, value]) => typeof value === 'string' ? value : '').filter(Boolean).join(' · ') : '';
export async function exportDraft(id: string, title: string) {
  const response = await fetch(`/api/drafts/${encodeURIComponent(id)}/export`, { method: 'POST', credentials: 'same-origin' });
  if (!response.ok) { const data = await response.json().catch(() => ({})); throw new Error(typeof data.detail === 'string' ? data.detail : 'Не удалось выгрузить документ.'); }
  const blob = await response.blob(); const url = URL.createObjectURL(blob); const link = document.createElement('a');
  link.href = url; link.download = `${title.replace(/[\\/:*?"<>|]/g, '').slice(0, 100) || 'Проект документа'}.docx`; document.body.appendChild(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 30000);
  return response.headers.get('X-Draft-Incomplete')?.trim().toLowerCase() === 'true';
}
