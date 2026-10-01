"""FastAPI front end for mixedgamesgto.com.

Serves the landing page from ``web/static`` and stands in front of the two
solvers, which run as their own services. Railway points one domain at one
service, so anything else sharing that domain has to be forwarded by hand:
a request under ``/holdem`` or ``/prlps`` is replayed against the matching
service and its answer handed straight back. Both solvers already mount
themselves under exactly those prefixes, so the path a browser asks for is
the path the upstream is asked for -- nothing is rewritten in between.
"""

import os
import re
from contextlib import asynccontextmanager
from html import escape
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

STATIC_DIR = Path(__file__).resolve().parent / "static"
HOME_PAGE = Path(__file__).resolve().parent / "home" / "index.html"

#: Path prefix -> base URL of the service that owns it, e.g.
#: ``https://holdem-production.up.railway.app``. A prefix left unset still
#: routes, it just answers 503 instead of leaving a dead link on the page.
UPSTREAMS = {
    "holdem": os.environ.get("HOLDEM_UPSTREAM", "").rstrip("/"),
    "hmrds": os.environ.get("HMRDS_UPSTREAM", "").rstrip("/"),
    "noah": os.environ.get("NOAH_UPSTREAM", "").rstrip("/"),
    "prlps": os.environ.get("PRLPS_UPSTREAM", "").rstrip("/"),
    "redriver": os.environ.get("REDRIVER_UPSTREAM", "").rstrip("/"),
}

#: Where the site lives. Unset, every host is served as it stands; set, any
#: host in MOVED_HOSTS is sent there instead, path and query intact. Left to
#: the environment so the old domain keeps working until the new one does.
CANONICAL_HOST = os.environ.get("CANONICAL_HOST", "").strip().lower()

#: Hosts the site has been served from and should now leave for the canonical
#: one. Only these move: Railway's own addresses and its health check keep
#: answering where they are asked.
MOVED_HOSTS = frozenset({
    "alexwezensky.com", "www.alexwezensky.com", "www.mixedgamesgto.com",
})

#: Hosts whose front page is the personal home page rather than a redirect.
#: Only the root stays; every other path still moves, so old solver links
#: on the old domain keep landing where they used to.
HOME_HOSTS = frozenset({"alexwezensky.com", "www.alexwezensky.com"})



def analytics_snippet(env=os.environ) -> str:
    """The tags for every analytics service configured, ready for <head>.

    Each is switched on by its own variable and left out without it:
    ``GA4_ID`` (G-XXXXXXXXXX), ``UMAMI_WEBSITE_ID`` (with ``UMAMI_SCRIPT_URL``
    for a self-hosted Umami) and ``GOATCOUNTER_CODE`` (the part before
    .goatcounter.com).
    """
    tags = []
    ga4 = env.get("GA4_ID", "").strip()
    if re.fullmatch(r"G-[A-Z0-9]+", ga4):
        tags.append(
            f'<script async src="https://www.googletagmanager.com/gtag/js?id={ga4}"></script>'
            "<script>window.dataLayer=window.dataLayer||[];"
            "function gtag(){dataLayer.push(arguments);}"
            f"gtag('js',new Date());gtag('config','{ga4}');</script>")
    umami = env.get("UMAMI_WEBSITE_ID", "").strip()
    if umami:
        src = env.get("UMAMI_SCRIPT_URL", "").strip() or "https://cloud.umami.is/script.js"
        tags.append(f'<script defer src="{escape(src)}" '
                    f'data-website-id="{escape(umami)}"></script>')
    # Taken however GoatCounter shows it: the code, its host, or its full URL.
    goat = env.get("GOATCOUNTER_CODE", "").strip().lower()
    goat = re.sub(r"^https?://", "", goat).split("/")[0].removesuffix(".goatcounter.com")
    if re.fullmatch(r"[a-z0-9-]+", goat):
        tags.append(f'<script data-goatcounter="https://{goat}.goatcounter.com/count" '
                    'async src="//gc.zgo.at/count.js"></script>')
    return "".join(tags)


#: Read once: the variables only change with a redeploy.
ANALYTICS = analytics_snippet()

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


app = FastAPI(title="mixedgamesgto.com", docs_url=None, redoc_url=None,
              lifespan=lifespan)


@app.middleware("http")
async def move_to_canonical(request: Request, call_next):
    """Send the old domain on to the new one, to the same page.

    308 rather than 301 so a solve POSTed to the old address is replayed as a
    POST, and permanent so search engines carry the old links over.
    """
    host = request.headers.get("host", "").split(":")[0].lower()
    if host in HOME_HOSTS and request.url.path == "/" and request.method in ("GET", "HEAD"):
        return FileResponse(HOME_PAGE)
    if CANONICAL_HOST and host in MOVED_HOSTS and host != CANONICAL_HOST:
        target = "https://" + CANONICAL_HOST + request.url.path
        if request.url.query:
            target += "?" + request.url.query
        return RedirectResponse(target, status_code=308)
    return await call_next(request)


@app.middleware("http")
async def add_analytics(request: Request, call_next):
    """Put the analytics tags into every page, ours and the solvers' alike.

    Everything public passes through here, so one set of variables covers all
    of it and no solver has to know analytics exist.
    """
    response = await call_next(request)
    if (not ANALYTICS or request.method != "GET" or response.status_code != 200
            or not response.headers.get("content-type", "").startswith("text/html")):
        return response
    body = b"".join([chunk async for chunk in response.body_iterator])
    body = body.replace(b"</head>", ANALYTICS.encode() + b"</head>", 1)
    # The validators described the page before the tags went in; left on, a
    # browser could keep a page from before the tags changed.
    headers = {k: v for k, v in response.headers.items()
               if k.lower() not in ("content-length", "etag", "last-modified")}
    return Response(content=body, status_code=response.status_code, headers=headers)


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

    Only the host is compared. The scheme is no use for telling the two apart:
    the upstream builds its redirects from the x-forwarded-proto it was handed,
    so the same address comes back as http over a plain hop and https over a
    real one, and a prefix match would quietly miss half the time.
    """
    parsed = urlsplit(location)
    if parsed.netloc and parsed.netloc == urlsplit(target).netloc:
        return urlunsplit(("", "", parsed.path, parsed.query, parsed.fragment)) or "/"
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
@app.get("/changelog", include_in_schema=False)
def changelog():
    """A page of its own without a trailing slash; the static mount
    would not resolve either without one."""
    return FileResponse(STATIC_DIR / "changelog.html")


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
