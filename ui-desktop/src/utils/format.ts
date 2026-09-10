export function formatRelativeTime(timestampSecondsOrMs: number): string {
  if (!timestampSecondsOrMs || timestampSecondsOrMs <= 0) {
    return '';
  }
  // If timestamp is in seconds (e.g. Unix timestamp < 1e11), convert to ms
  const ms = timestampSecondsOrMs < 1e11 ? timestampSecondsOrMs * 1000 : timestampSecondsOrMs;
  const now = Date.now();
  const diffSec = Math.floor((now - ms) / 1000);

  if (diffSec < 10) {
    return '刚刚';
  }
  if (diffSec < 60) {
    return `${diffSec}秒前`;
  }
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) {
    return `${diffMin}分钟前`;
  }
  const diffHour = Math.floor(diffMin / 60);
  if (diffHour < 24) {
    return `${diffHour}小时前`;
  }
  const diffDay = Math.floor(diffHour / 24);
  if (diffDay === 1) {
    return '昨天';
  }
  if (diffDay < 7) {
    return `${diffDay}天前`;
  }
  const date = new Date(ms);
  const month = date.getMonth() + 1;
  const day = date.getDate();
  return `${month}月${day}日`;
}

export function formatTruncated(text: string, maxLength: number): string {
  if (!text) return '';
  if (text.length <= maxLength) return text;
  return text.slice(0, maxLength) + '...';
}

export function safeJsonStringify(data: unknown): string {
  if (typeof data === 'string') return data;
  try {
    return JSON.stringify(data, null, 2);
  } catch {
    return String(data);
  }
}
