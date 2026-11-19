"""
Fake Pelican federation, for local development and tests.

Plays both Pelican roles the catalog side ever talks to:

  - federation + registry: serves /.well-known/pelican-configuration and the
    per-namespace issuer.jwks that verify_pelican_event fetches signing keys
    from (see pelican_metadata.discover_registry / fetch_namespace_jwks)
  - origin: mints ES256 JWTs carrying the pelican.metadata:<namespace> scope
    and POSTs object.committed events to the webhook

That means the whole receive path - including real signature, audience,
expiry and scope checks - runs with no real Pelican and no tunnel.

Two ways to use it:

  Tests (no network at all):
      fake = FakePelican()
      fake.install(monkeypatch)          # patches requests.get + receiver config
      token = fake.mint_token(namespace) # sign as the origin
      event = fake.make_event(namespace, path, dataset="...")

  Local dev, inside the ckan-dev container (bench .env must have
  PELICAN_TRUSTED_FEDERATION=http://127.0.0.1:8099):
      python -m ckanext.ndpcatalogadditions.tests.fake_pelican serve &
      python -m ckanext.ndpcatalogadditions.tests.fake_pelican send \\
          --path /fake-namespace/a.csv --meta dataset=demo --meta expected_count=1

Only imports jose/ecdsa/requests (no ckan), so the CLI starts instantly. The
CLI keeps its signing key in a gitignored .fake_pelican/ at the repo root so
`serve` and `send` (separate processes) agree on it.
"""

import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import requests
from ecdsa import NIST256p, SigningKey
from jose import jwk, jwt

DEFAULT_FEDERATION = "http://127.0.0.1:8099"
DEFAULT_NAMESPACE = "/fake-namespace"
DEFAULT_ORIGIN = "https://fake-origin.test"
DEFAULT_KID = "fake-pelican-key-1"
REGISTRY_PREFIX = "/api/v1.0/registry"
JWKS_SUFFIX = "/.well-known/issuer.jwks"

REPO_ROOT = Path(__file__).resolve().parents[3]
KEY_DIR = Path(os.getenv("FAKE_PELICAN_HOME", REPO_ROOT / ".fake_pelican"))


def generate_private_pem():
    return SigningKey.generate(curve=NIST256p).to_pem().decode()


class FakePelican:
    def __init__(
        self,
        federation=DEFAULT_FEDERATION,
        origin=DEFAULT_ORIGIN,
        kid=DEFAULT_KID,
        private_pem=None,
        namespaces=None,
    ):
        self.federation = federation.rstrip("/")
        # A real federation's registry lives elsewhere; serving both from one
        # base keeps the fake to a single port.
        self.registry = self.federation
        self.origin = origin
        self.kid = kid
        self.private_pem = private_pem or generate_private_pem()
        # None = registry knows every namespace; a set = 404 for the rest,
        # like a real registry for an unregistered namespace.
        self.namespaces = set(namespaces) if namespaces is not None else None
        # Flip to simulate a registry outage.
        self.registry_down = False

    # ---- registry / federation side -------------------------------------

    def public_jwk(self):
        key = jwk.construct(self.private_pem, "ES256").public_key().to_dict()
        key.update({"kid": self.kid, "use": "sig", "alg": "ES256"})
        return key

    def handle_get(self, url):
        """Route a GET the receiver would make. Returns (status, json_body)."""
        if self.registry_down:
            return 503, {"error": "registry unavailable"}

        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        path = parsed.path

        if base == self.federation and path == "/.well-known/pelican-configuration":
            return 200, {
                "issuer": self.federation,
                "director_endpoint": self.federation,
                "namespace_registration_endpoint": self.registry,
                "jwks_uri": f"{self.federation}/.well-known/issuer.jwks",
            }

        if base == self.registry and path.startswith(REGISTRY_PREFIX) and path.endswith(JWKS_SUFFIX):
            namespace = path[len(REGISTRY_PREFIX):-len(JWKS_SUFFIX)]
            if self.namespaces is not None and namespace not in self.namespaces:
                return 404, {"error": f"namespace not registered: {namespace}"}
            return 200, {"keys": [self.public_jwk()]}

        return 404, {"error": f"not found: {url}"}

    def install(self, monkeypatch, webhook_url="http://catalog.test/webhook", trust=True):
        """
        Point pelican_metadata at this fake for one test: answer its
        requests.get calls in-process, set its trusted federation and expected
        audience, and clear its key caches so nothing leaks between tests.
        """
        from ckanext.ndpcatalogadditions import pelican_metadata

        self.webhook_url = webhook_url
        # pelican_metadata.requests *is* the global requests module, so this
        # patch is seen by every caller (CKAN's own solr checks included) -
        # only answer our own urls and pass everything else through.
        real_get = requests.get

        def fake_get(url, *args, **kwargs):
            if url.startswith(self.federation) or url.startswith(self.registry):
                status, body = self.handle_get(url)
                return _FakeResponse(url, status, body)
            return real_get(url, *args, **kwargs)

        monkeypatch.setattr(pelican_metadata.requests, "get", fake_get)
        monkeypatch.setattr(pelican_metadata, "TRUSTED_FEDERATION", self.federation if trust else None)
        monkeypatch.setattr(pelican_metadata, "WEBHOOK_URL", webhook_url)
        monkeypatch.setattr(pelican_metadata, "_registry_cache", {})
        monkeypatch.setattr(pelican_metadata, "_jwks_cache", {})
        return self

    # ---- origin side ----------------------------------------------------

    def mint_token(
        self,
        namespace=DEFAULT_NAMESPACE,
        audience=None,
        scope=None,
        lifetime=300,
        private_pem=None,
        kid=None,
        extra_claims=None,
    ):
        """
        Sign a webhook token the way a Pelican origin does. Every knob is
        there so tests can break exactly one thing: lifetime<0 for expired,
        private_pem= another key for a bad signature, scope= for the wrong
        namespace, audience= for a token meant for some other catalog.
        """
        now = int(time.time())
        claims = {
            "iss": self.origin,
            "sub": self.origin,
            "aud": audience or getattr(self, "webhook_url", None),
            "scope": scope if scope is not None else f"pelican.metadata:{namespace}",
            "iat": now,
            "nbf": now,
            "exp": now + lifetime,
            "jti": str(uuid.uuid4()),
            "wlcg.ver": "1.0",
        }
        claims.update(extra_claims or {})
        return jwt.encode(
            claims,
            private_pem or self.private_pem,
            algorithm="ES256",
            headers={"kid": kid or self.kid},
        )

    def make_event(self, namespace=DEFAULT_NAMESPACE, path=None, federation=None, **custom):
        """An object.committed event as Pelican's spec shapes it."""
        path = path or f"{namespace}/file-{uuid.uuid4().hex[:8]}.csv"
        return {
            "id": str(uuid.uuid4()),
            "type": "object.committed",
            "time": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "federation": federation or self.federation,
            "namespace": namespace,
            "object": {
                "path": path,
                "size": 1024,
                "etag": f'"{uuid.uuid4().hex}"',
                "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                **custom,
            },
        }

    # ---- local dev server -----------------------------------------------

    def serve(self, host="127.0.0.1", port=8099):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                status, body = fake.handle_get(f"{fake.federation}{self.path}")
                payload = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, fmt, *args):
                sys.stderr.write(f"[fake-pelican] {self.address_string()} {fmt % args}\n")

        server = ThreadingHTTPServer((host, port), Handler)
        print(f"[fake-pelican] federation + registry at {self.federation} (listening on {host}:{port})", flush=True)
        server.serve_forever()


class _FakeResponse:
    def __init__(self, url, status_code, body):
        self.url = url
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} for {self.url}", response=self)


# ---- CLI ----------------------------------------------------------------

def load_or_create_key(key_dir=KEY_DIR):
    key_dir.mkdir(parents=True, exist_ok=True)
    key_file = key_dir / "key.pem"
    if not key_file.exists():
        key_file.write_text(generate_private_pem())
        key_file.chmod(0o600)
    return key_file.read_text()


def parse_meta(pairs):
    meta = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise SystemExit(f"--meta expects key=value, got: {pair}")
        key, value = pair.split("=", 1)
        meta[key] = value
    return meta


def main(argv=None):
    parser = argparse.ArgumentParser(prog="fake_pelican", description=__doc__.split("\n\n")[0])
    parser.add_argument("--federation", default=DEFAULT_FEDERATION)
    sub = parser.add_subparsers(dest="command", required=True)

    p_serve = sub.add_parser("serve", help="serve discovery + JWKS for the receiver to fetch")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8099)

    p_send = sub.add_parser("send", help="sign and POST one object.committed event")
    p_send.add_argument("--url", default="http://127.0.0.1:5000/", help="webhook to POST to")
    p_send.add_argument(
        "--aud",
        default=os.getenv("CKANEXT__NDPCATALOGADDITIONS__PELICAN_WEBHOOK_URL"),
        help="token audience (default: the receiver's PELICAN_WEBHOOK_URL env)",
    )
    p_send.add_argument("--namespace", default=DEFAULT_NAMESPACE)
    p_send.add_argument("--path", help="object path, must start with the namespace")
    p_send.add_argument("--meta", action="append", metavar="KEY=VALUE", help="custom object metadata, repeatable")
    p_send.add_argument("--event-federation", help="override the event's federation field")
    p_send.add_argument("--scope", help="override the token scope")
    p_send.add_argument("--expired", action="store_true", help="send an already-expired token")
    p_send.add_argument("--bad-signature", action="store_true", help="sign with a key the registry doesn't serve")
    p_send.add_argument("--dry-run", action="store_true", help="print the event and token instead of sending")

    args = parser.parse_args(argv)
    fake = FakePelican(federation=args.federation, private_pem=load_or_create_key())

    if args.command == "serve":
        fake.serve(args.host, args.port)
        return 0

    if not args.aud:
        raise SystemExit("no audience: pass --aud or set CKANEXT__NDPCATALOGADDITIONS__PELICAN_WEBHOOK_URL")

    event = fake.make_event(
        args.namespace, args.path, federation=args.event_federation, **parse_meta(args.meta)
    )
    token = fake.mint_token(
        args.namespace,
        audience=args.aud,
        scope=args.scope,
        lifetime=-60 if args.expired else 300,
        private_pem=generate_private_pem() if args.bad_signature else None,
    )

    if args.dry_run:
        print(json.dumps(event, indent=2))
        print(token)
        return 0

    response = requests.post(
        args.url,
        json=event,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    print(f"{response.status_code} {response.text.strip()}")
    return 0 if response.ok else 1


if __name__ == "__main__":
    sys.exit(main())
