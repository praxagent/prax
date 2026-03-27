// HTTP + WebSocket reverse proxy for Chrome DevTools Protocol.
// Chrome only accepts Host: localhost or an IP — rewrites the header.
const http = require('http');
const net = require('net');

const CDP_HOST = '127.0.0.1';
const CDP_PORT = 9222;
const LISTEN_PORT = 9223;

// HTTP proxy (for /json/* endpoints)
const server = http.createServer((req, res) => {
  const opts = {
    hostname: CDP_HOST,
    port: CDP_PORT,
    path: req.url,
    method: req.method,
    headers: { ...req.headers, host: `${CDP_HOST}:${CDP_PORT}` },
  };
  const proxy = http.request(opts, (upRes) => {
    // Rewrite WebSocket URLs in JSON responses so they point to
    // the external port, not Chrome's internal 127.0.0.1:9222.
    let body = '';
    upRes.on('data', (chunk) => (body += chunk));
    upRes.on('end', () => {
      body = body
        .replace(/127\.0\.0\.1:9222/g, `${req.headers.host}`)
        .replace(/localhost:9222/g, `${req.headers.host}`);
      const headers = { ...upRes.headers };
      headers['content-length'] = Buffer.byteLength(body);
      res.writeHead(upRes.statusCode, headers);
      res.end(body);
    });
  });
  proxy.on('error', (e) => {
    res.writeHead(502);
    res.end(`CDP proxy error: ${e.message}`);
  });
  req.pipe(proxy);
});

// WebSocket upgrade proxy (for devtools WS connections)
server.on('upgrade', (req, socket, head) => {
  const upstream = net.connect(CDP_PORT, CDP_HOST, () => {
    // Rewrite the upgrade request with correct Host header
    const path = req.url;
    const headers = Object.entries(req.headers)
      .map(([k, v]) => (k.toLowerCase() === 'host' ? `${k}: ${CDP_HOST}:${CDP_PORT}` : `${k}: ${v}`))
      .join('\r\n');
    upstream.write(
      `GET ${path} HTTP/1.1\r\n${headers}\r\n\r\n`
    );
    if (head.length) upstream.write(head);
    socket.pipe(upstream);
    upstream.pipe(socket);
  });
  upstream.on('error', () => socket.destroy());
  socket.on('error', () => upstream.destroy());
});

server.listen(LISTEN_PORT, '0.0.0.0', () => {
  console.log(`CDP proxy listening on 0.0.0.0:${LISTEN_PORT} -> ${CDP_HOST}:${CDP_PORT}`);
});
