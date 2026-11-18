#!/usr/bin/env python3
"""Header relay between Agensh's egress proxy and the Anthropic Messages API.

Global context: Agensh's egress proxy (scripts/strip_metadata_proxy.py in
microsoft/Agensh) injects the model credential only as `Authorization: Bearer
<token>` and cannot add other headers. Our API key is not scoped to a
workspace, so the API rejects every request without an `anthropic-workspace-id`
header ("This API key is not scoped to a workspace, so this request must
include the anthropic-workspace-id header"). This relay sits where Agensh's
REPRODUCE.md puts a host relay ("Host-loopback model APIs": listen on the Docker
host gateway, set LLM_UPSTREAM to it). It moves the Bearer token into
`x-api-key` (the API-key header in https://docs.anthropic.com/en/api/overview),
adds the workspace header, and otherwise passes bytes through unchanged.

It never stores or logs credentials: the key arrives with each request from the
egress proxy. Logs carry only method, path, status, byte counts and duration.

Environment: ANTHROPIC_WORKSPACE_ID (required), LISTEN_HOST (default
172.17.0.1, docker0), LISTEN_PORT (default 8788), UPSTREAM_HOST (default
api.anthropic.com), UPSTREAM_TIMEOUT_SECONDS (default 600, like Agensh's proxy).
"""

import http.client
import http.server
import os
import sys
import time

WORKSPACE = os.environ['ANTHROPIC_WORKSPACE_ID'].strip()  # fail fast if missing
UPSTREAM_HOST = os.environ.get('UPSTREAM_HOST', 'api.anthropic.com')
TIMEOUT = float(os.environ.get('UPSTREAM_TIMEOUT_SECONDS', '600'))
# RFC 9110 section 7.6.1 hop-by-hop fields, plus fields the relay rewrites itself.
DROP = {'connection', 'keep-alive', 'proxy-connection', 'te', 'trailer', 'transfer-encoding',
        'upgrade', 'host', 'content-length', 'authorization', 'x-api-key', 'anthropic-workspace-id'}


def log(message):
    print(time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()) + ' ' + message, file=sys.stderr, flush=True)


class Relay(http.server.BaseHTTPRequestHandler):
    # HTTP/1.0 (the BaseHTTPRequestHandler default) closes after each response, so a
    # body without Content-Length can simply be streamed until EOF.
    # https://docs.python.org/3/library/http.server.html#http.server.BaseHTTPRequestHandler.protocol_version

    def do_POST(self):
        self.relay()

    def do_GET(self):
        self.relay()

    def log_message(self, fmt, *args):  # replace the default access log (it is unstructured)
        pass

    def relay(self):
        started = time.monotonic()
        body = self.rfile.read(int(self.headers.get('Content-Length') or 0))  # the egress proxy always sets it
        headers = {k: v for k, v in self.headers.items() if k.lower() not in DROP}
        auth = self.headers.get('Authorization', '')
        token = auth[7:].strip() if auth.lower().startswith('bearer ') else self.headers.get('x-api-key', '')
        if token:
            headers['x-api-key'] = token
        headers['anthropic-workspace-id'] = WORKSPACE
        headers['Content-Length'] = str(len(body))
        try:
            # https://docs.python.org/3/library/http.client.html#http.client.HTTPSConnection
            upstream = http.client.HTTPSConnection(UPSTREAM_HOST, timeout=TIMEOUT)
            upstream.request(self.command, self.path, body=body, headers=headers)
            response = upstream.getresponse()
        except OSError as error:
            log(f'{self.command} {self.path} -> 502 upstream error: {error}')
            self.send_error(502, f'relay upstream error: {error}')
            return
        self.send_response_only(response.status, response.reason)
        for key, value in response.getheaders():
            if key.lower() not in ('connection', 'transfer-encoding', 'keep-alive'):
                self.send_header(key, value)
        self.end_headers()
        sent = 0
        try:
            # read1 returns as soon as bytes arrive, so SSE events pass through unbuffered.
            while chunk := response.read1(65536):
                self.wfile.write(chunk)
                self.wfile.flush()
                sent += len(chunk)
        except OSError as error:
            log(f'{self.command} {self.path} stream interrupted after {sent}b: {error}')
        finally:
            upstream.close()
        log(f'{self.command} {self.path} in={len(body)}b -> {response.status} out={sent}b '
            f'{time.monotonic() - started:.1f}s')


def main():
    host = os.environ.get('LISTEN_HOST', '172.17.0.1')
    port = int(os.environ.get('LISTEN_PORT', '8788'))
    server = http.server.ThreadingHTTPServer((host, port), Relay)  # one thread per request
    log(f'anthropic relay listening on http://{host}:{port} -> https://{UPSTREAM_HOST} '
        f'(workspace header set, Bearer -> x-api-key)')
    server.serve_forever()


if __name__ == '__main__':
    main()
