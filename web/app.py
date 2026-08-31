"""FastAPI front end for alexwezensky.com.

Serves the landing page from ``web/static`` and stands in front of the two
solvers, which run as their own services. Railway points one domain at one
service, so anything else sharing that domain has to be forwarded by hand:
a request under ``/holdem`` or ``/hmrs`` is replayed against the matching
service and its answer handed straight back. Both solvers already mount
themselves under exactly those prefixes, so the path a browser asks for is
the path the upstream is asked for -- nothing is rewritten in between.
"""

import os
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

STATIC_DIR = Path(__file__).resolve().parent / "static"

#: Path prefix -> base URL of the service that owns it, e.g.
#: ``https://holdem-production.up.railway.app``. A prefix left unset still
#: routes, it just answers 503 instead of leaving a dead link on the page.
UPSTREAMS = {
    "holdem": os.environ.get("HOLDEM_UPSTREAM", "").rstrip("/"),
    "hmrs": os.environ.get("HMRS_UPSTREAM", "").rstrip("/"),
}

METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]

#: Headers that describe one leg of a connection rather than the message, so
#: they must not be copied onto the next leg. ``content-encoding`` and
#: ``content-length`` join them on the way back because httpx hands us the
#: body already decoded, which would leave both of them lying.
HOP_BY_HOP = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "upgrade",
})
DROP_FROM_RESPONSE = HOP_BY_HOP | {"content-encoding", "content-length"}

#: Connecting should be quick; a solve should not be rushed. An exact preflop
#: walk is allowed several seconds, so the read budget is generous.
TIMEOUT = httpx.Timeout(10.0, read=120.0)


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False) as client:
        app.state.client = client
        yield


app = FastAPI(title="alexwezensky.com", docs_url=None, redoc_url=None,
              lifespan=lifespan)


@app.get("/api/health", include_in_schema=False)
def health():
    return {
        "status": "ok",
        "upstreams": {name: bool(url) for name, url in UPSTREAMS.items()},
    }


def local_location(location: str, target: str) -> str:
    """Bring a redirect that names the upstream back onto our own domain.

    A relative Location already points at the right place, since the paths on
    both sides match. An absolute one carries the upstream's address, which a
    browser cannot follow to a solver that has no domain of its own -- so only
    the path survives.
    """
    if location.startswith(target):
        return location[len(target):] or "/"
    return location


async def proxy(request: Request) -> Response:
    """Replay one request against the service that owns its prefix."""
    path = request.url.path
    prefix = path.split("/")[1]
    target = UPSTREAMS.get(prefix)
    if not target:
        return JSONResponse(
            {"detail": f"The {prefix} solver is not configured on this host."},
            status_code=503,
        )

    url = target + path
    if request.url.query:
        url = f"{url}?{request.url.query}"

    # Host has to name the upstream, not us: Railway's edge picks the service
    # to hand a request to by reading it, so forwarding our own would either
    # miss the solver entirely or route straight back here. Where the caller
    # came from is carried by the x-forwarded-* pair instead.
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_BY_HOP}
    headers["host"] = urlsplit(target).netloc
    headers["x-forwarded-proto"] = request.url.scheme
    headers["x-forwarded-host"] = request.headers.get("host", "")

    try:
        upstream = await request.app.state.client.request(
            request.method, url, headers=headers, content=await request.body()
        )
    except httpx.TimeoutException:
        return JSONResponse({"detail": f"The {prefix} solver timed out."}, status_code=504)
    except httpx.RequestError:
        return JSONResponse({"detail": f"The {prefix} solver is unreachable."}, status_code=502)

    headers = {k: v for k, v in upstream.headers.items()
               if k.lower() not in DROP_FROM_RESPONSE}
    if "location" in headers:
        headers["location"] = local_location(headers["location"], target)

    return Response(content=upstream.content, status_code=upstream.status_code,
                    headers=headers)


for _name in UPSTREAMS:
    # Both shapes are needed: the bare prefix is what a browser asks for, and
    # the upstream answers it with the redirect that adds the trailing slash.
    app.router.add_route(f"/{_name}", proxy, methods=METHODS)
    app.router.add_route(f"/{_name}/{{path:path}}", proxy, methods=METHODS)


# Mounted last so the routes above win; ``html=True`` serves index.html at /.
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
