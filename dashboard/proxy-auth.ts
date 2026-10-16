/** The dev/preview proxy lends the backend credential only to requests from this machine. */

export type Req = { socket: { remoteAddress?: string } };
export type ProxyReq = { setHeader(name: string, value: string): void };

const LOOPBACK = new Set(['127.0.0.1', '::1', '::ffff:127.0.0.1']);

export const isLoopback = (address?: string): boolean => !!address && LOOPBACK.has(address);

/** A handler for http-proxy's `proxyReq`/`proxyReqWs` that adds the key for loopback callers only. */
export function makeKeyLender(apiKey: string) {
  return (proxyReq: ProxyReq, req: Req): void => {
    if (apiKey && isLoopback(req.socket.remoteAddress)) {
      proxyReq.setHeader('Authorization', `Bearer ${apiKey}`);
    }
  };
}
