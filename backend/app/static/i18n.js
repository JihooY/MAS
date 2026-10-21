const data = JSON.parse(document.getElementById('mas-i18n')?.textContent || '{}');
export const language = data.lang === 'en' ? 'en' : 'ko';
export const locale = language === 'en' ? 'en-US' : 'ko-KR';
const messages = Object.assign(Object.create(null), data.messages || {});
const placeholder = /\{(\w+)\}/g;
const interpolate = (text, values) => text.replace(placeholder, (whole, key) => String(values[key] ?? whole));
const patterns = Object.entries(messages).filter(([key]) => /\{\w+\}/.test(key)).map(([key, target]) => {
  const names = [];
  const escaped = key.split(/(\{\w+\})/).map(part => {
    if (/^\{\w+\}$/.test(part)) { names.push(part.slice(1, -1)); return '(.+?)'; }
    return part.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  }).join('');
  return { regex: new RegExp(`^${escaped}$`, 's'), names, target };
});
export function t(message, values = {}) {
  const text = String(message ?? '');
  let target = messages[text];
  if (target === undefined && language === 'en') {
    for (const pattern of patterns) {
      const match = text.match(pattern.regex);
      if (!match) continue;
      target = pattern.target;
      values = { ...Object.fromEntries(pattern.names.map((name, i) => [name, messages[match[i + 1]] ?? match[i + 1]])), ...values };
      break;
    }
  }
  return interpolate(target ?? text, values);
}
document.addEventListener('change', event => {
  const select = event.target.closest('[data-language-select]');
  if (!select || select.value === language) return;
  if (select.id === 'settings-language') {
    try { sessionStorage.setItem('mas-reopen-language-settings', '1'); } catch { /* Storage is optional. */ }
  }
  select.form.requestSubmit();
});
