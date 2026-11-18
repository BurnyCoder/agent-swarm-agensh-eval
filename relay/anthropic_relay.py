#!/usr/bin/env python3
"""Header relay with a hard dollar cap between Agensh's egress proxy and the Anthropic Messages API.

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

Spend cap: Agensh's own `max_model_tokens` counts only uncached input + output
tokens (scripts/usage_meter.py), not cache reads/writes or dollars. Every model
request of every team passes through this relay, so the cap is enforced here:
- Only RELAY_ALLOWED_MODELS (default claude-haiku-5-5) are forwarded, so the price card is known.
- Before forwarding, the relay reserves an upper bound of the request's cost
  (input estimated as body bytes / 2 tokens, all billed as 1-hour cache writes, plus
  max_tokens of output, on the long-prompt price card if the estimate exceeds 100K tokens).
  If spent + reserved + this bound would exceed RELAY_SPEND_CAP_USD, it answers 403
  without contacting the API. Concurrent requests therefore cannot overshoot the cap.
- After the response, the reservation is replaced by the actual cost computed from the
  response's `usage` (JSON body or SSE message_start/message_delta events). If usage cannot
  be read from a successful response, the full reservation is charged.
- The ledger persists in RELAY_LEDGER (JSON) and starts from RELAY_SEED_USD the first time,
  so spend before the relay had a cap is counted.
Prices (Claude Haiku 5.5, claude-api skill pricing notes): $0.10 / $0.50 per MTok input / output
for prompts up to 100K tokens, $0.50 / $2.50 above; cache reads 0.1x input, 5-minute cache
writes 1.25x, 1-hour writes 2x.

It never stores or logs credentials. Logs carry method, path, status, sizes, cost and duration.

Environment: ANTHROPIC_WORKSPACE_ID (required), LISTEN_HOST (default 172.17.0.1),
LISTEN_PORT (8788), UPSTREAM_HOST (api.anthropic.com), UPSTREAM_TIMEOUT_SECONDS (600),
RELAY_SPEND_CAP_USD (unset: no cap), RELAY_SEED_USD (0), RELAY_LEDGER
(/root/.agensh/relay_spend.json), RELAY_ALLOWED_MODELS (claude-haiku-5-5).
"""

import http.client
import http.server
import json
import os
import sys
import threading
import time
from pathlib import Path

WORKSPACE = os.environ['ANTHROPIC_WORKSPACE_ID'].strip()  # fail fast if missing
UPSTREAM_HOST = os.environ.get('UPSTREAM_HOST', 'api.anthropic.com')
TIMEOUT = float(os.environ.get('UPSTREAM_TIMEOUT_SECONDS', '600'))
CAP = float(os.environ['RELAY_SPEND_CAP_USD']) if os.environ.get('RELAY_SPEND_CAP_USD') else None
LEDGER = Path(os.environ.get('RELAY_LEDGER', '/root/.agensh/relay_spend.json'))
ALLOWED = {m.strip() for m in os.environ.get('RELAY_ALLOWED_MODELS', 'claude-haiku-5-5').split(',') if m.strip()}
# RFC 9110 section 7.6.1 hop-by-hop fields, plus fields the relay rewrites itself.
DROP = {'connection', 'keep-alive', 'proxy-connection', 'te', 'trailer', 'transfer-encoding',
        'upgrade', 'host', 'content-length', 'authorization', 'x-api-key', 'anthropic-workspace-id',
        'accept-encoding'}  # identity responses, so usage can be read from the bytes
# USD per token: (input, cache read, 5-minute cache write, 1-hour cache write, output).
SHORT = tuple(p / 1e6 for p in (0.10, 0.01, 0.125, 0.20, 0.50))
LONG = tuple(p / 1e6 for p in (0.50, 0.05, 0.625, 1.00, 2.50))
LONG_PROMPT = 100_000


def log(message):
    print(time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()) + ' ' + message, file=sys.stderr, flush=True)


def actual_cost(usage):
    """Dollar cost of one response's usage block (Messages API field names)."""
    read = usage.get('cache_read_input_tokens') or 0
    write = usage.get('cache_creation_input_tokens') or 0
    fresh = usage.get('input_tokens') or 0
    card = LONG if fresh + read + write > LONG_PROMPT else SHORT
    split = usage.get('cache_creation') or {}
    w5, w1 = split.get('ephemeral_5m_input_tokens'), split.get('ephemeral_1h_input_tokens')
    if w5 is None and w1 is None:
        w5, w1 = 0, write  # TTL unknown: bill every write at the 1-hour (higher) rate
    return (fresh * card[0] + read * card[1] + (w5 or 0) * card[2] + (w1 or 0) * card[3]
            + (usage.get('output_tokens') or 0) * card[4])


def usage_from(data, streamed):
    """Merge the usage blocks found in a JSON body or an SSE stream; None if there are none."""
    if not streamed:
        try:
            return json.loads(data).get('usage')
        except (ValueError, AttributeError):
            return None
    usage = None
    for line in data.split(b'\n'):
        if not line.startswith(b'data:'):
            continue
        try:
            event = json.loads(line[5:])
        except ValueError:
            continue
        block = (event.get('message') or {}).get('usage') if event.get('type') == 'message_start' else event.get('usage')
        if isinstance(block, dict):
            usage = {**(usage or {}), **{k: v for k, v in block.items() if v is not None}}
    return usage


class Budget:
    """Thread-safe spend ledger with reservations; persisted after every change."""

    def __init__(self):
        self.lock = threading.Lock()
        self.reserved = 0.0
        state = json.loads(LEDGER.read_text()) if LEDGER.is_file() else {}
        self.spent = float(state.get('spent_usd', os.environ.get('RELAY_SEED_USD', '0') or 0))
        self.requests = int(state.get('requests', 0))
        self.save()

    def save(self):
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        temporary = LEDGER.with_suffix('.tmp')
        temporary.write_text(json.dumps({'spent_usd': round(self.spent, 6), 'requests': self.requests,
                                         'cap_usd': CAP, 'updated': time.time()}))
        temporary.replace(LEDGER)

    def reserve(self, bound):
        with self.lock:
            if CAP is not None and self.spent + self.reserved + bound > CAP:
                return False
            self.reserved += bound
            return True

    def settle(self, bound, cost):
        with self.lock:
            self.reserved -= bound
            self.spent += cost
            self.requests += 1
            self.save()
            return self.spent


BUDGET = Budget()


class Relay(http.server.BaseHTTPRequestHandler):
    # HTTP/1.0 (the BaseHTTPRequestHandler default) closes after each response, so a
    # body without Content-Length can simply be streamed until EOF.
    # https://docs.python.org/3/library/http.server.html#http.server.BaseHTTPRequestHandler.protocol_version

    def do_POST(self):
        self.relay()

    def do_GET(self):
        if self.path == '/relay/spend':  # local monitoring endpoint
            body = json.dumps({'spent_usd': BUDGET.spent, 'reserved_usd': BUDGET.reserved,
                               'requests': BUDGET.requests, 'cap_usd': CAP}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.relay()

    def log_message(self, fmt, *args):  # replace the default access log (it is unstructured)
        pass

    def refuse(self, status, kind, message):
        body = json.dumps({'type': 'error', 'error': {'type': kind, 'message': message}}).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        log(f'{self.command} {self.path} -> {status} refused: {message}')

    def bound(self, body):
        """Upper bound of a request's cost; 0 for endpoints that are not billed (count_tokens, models)."""
        path = self.path.split('?', 1)[0].rstrip('/')
        if self.command != 'POST' or not path.endswith('/messages'):
            return 0.0, None
        try:
            request = json.loads(body)
        except ValueError:
            request = {}
        estimate = len(body) / 2  # generous: English JSON averages ~4 bytes per token
        card = LONG if estimate > LONG_PROMPT else SHORT
        return estimate * card[3] + int(request.get('max_tokens') or 128_000) * card[4], request.get('model')

    def relay(self):
        started = time.monotonic()
        body = self.rfile.read(int(self.headers.get('Content-Length') or 0))  # the egress proxy always sets it
        bound, model = self.bound(body)
        if bound and model not in ALLOWED:
            return self.refuse(403, 'permission_error', f'relay: model {model!r} is not allowed')
        if not BUDGET.reserve(bound):
            return self.refuse(403, 'permission_error',
                               f'relay: spend cap ${CAP:.2f} reached (spent ${BUDGET.spent:.4f})')
        headers = {k: v for k, v in self.headers.items() if k.lower() not in DROP}
        auth = self.headers.get('Authorization', '')
        token = auth[7:].strip() if auth.lower().startswith('bearer ') else self.headers.get('x-api-key', '')
        if token:
            headers['x-api-key'] = token
        headers['anthropic-workspace-id'] = WORKSPACE
        headers['Content-Length'] = str(len(body))
        captured, sent, status = bytearray(), 0, 502
        try:
            try:
                # https://docs.python.org/3/library/http.client.html#http.client.HTTPSConnection
                upstream = http.client.HTTPSConnection(UPSTREAM_HOST, timeout=TIMEOUT)
                upstream.request(self.command, self.path, body=body, headers=headers)
                response = upstream.getresponse()
            except OSError as error:
                log(f'{self.command} {self.path} -> 502 upstream error: {error}')
                self.send_error(502, f'relay upstream error: {error}')
                return
            status = response.status
            streamed = 'text/event-stream' in (response.getheader('Content-Type') or '')
            self.send_response_only(response.status, response.reason)
            for key, value in response.getheaders():
                if key.lower() not in ('connection', 'transfer-encoding', 'keep-alive'):
                    self.send_header(key, value)
            self.end_headers()
            try:
                # read1 returns as soon as bytes arrive, so SSE events pass through unbuffered.
                while chunk := response.read1(65536):
                    if bound:
                        captured += chunk
                    self.wfile.write(chunk)
                    self.wfile.flush()
                    sent += len(chunk)
            except OSError as error:
                log(f'{self.command} {self.path} stream interrupted after {sent}b: {error}')
            finally:
                upstream.close()
        finally:
            cost = 0.0
            if bound and status < 400:
                usage = usage_from(bytes(captured), streamed)
                cost = actual_cost(usage) if usage else bound  # unreadable usage: charge the upper bound
            total = BUDGET.settle(bound, cost)
            log(f'{self.command} {self.path} in={len(body)}b -> {status} out={sent}b '
                f'{time.monotonic() - started:.1f}s cost=${cost:.5f} total=${total:.4f}')


def main():
    host = os.environ.get('LISTEN_HOST', '172.17.0.1')
    port = int(os.environ.get('LISTEN_PORT', '8788'))
    server = http.server.ThreadingHTTPServer((host, port), Relay)  # one thread per request
    log(f'anthropic relay listening on http://{host}:{port} -> https://{UPSTREAM_HOST} '
        f'(workspace header, Bearer -> x-api-key, models {sorted(ALLOWED)}, '
        f'cap {"none" if CAP is None else f"${CAP:.2f}"}, spent so far ${BUDGET.spent:.4f})')
    server.serve_forever()


if __name__ == '__main__':
    main()
