export function getLiveWebSocketUrl(location?: Pick<Location, 'protocol' | 'host'>): string {
  if (!location) return 'ws://localhost/ws/live';
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${protocol}//${location.host}/ws/live`;
}
