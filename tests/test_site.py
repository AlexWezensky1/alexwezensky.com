"""Tests for the landing page and the proxy in front of the solvers.

The proxy is only worth anything if a request survives the extra hop intact,
so the upstream here is a real server on a real socket rather than a stub
transport: methods, bodies, query strings, headers, status codes and
redirects all have to come back out the other side unchanged.
"""

import json
import os
import socket
import sys
import threading
import time
import unittest
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse, RedirectResponse
from starlette.routing import Route

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


# The upstream stands in for the Hold'em service: it answers under /holdem,
# exactly as the real one mounts itself, and reports back what it was handed.
async def echo(request):
    return JSONResponse({
        "method": request.method,
        "path": request.url.path,
        "query": request.url.query,
        "body": (await request.body()).decode(),
        "host": request.headers.get("host"),
        "forwarded_host": request.headers.get("x-forwarded-host"),
        "proto": request.headers.get("x-forwarded-proto"),
        "te": request.headers.get("te"),
        "upgrade": request.headers.get("upgrade"),
        "custom": request.headers.get("x-seat"),
    })


async def page(request):
    return PlainTextResponse("<!DOCTYPE html><head><title>upstream</title></head>",
                             media_type="text/html")


async def to_slash(request):
    return RedirectResponse("/holdem/", status_code=307)


async def to_itself(request):
    """Redirects the way a server behind a proxy often does: absolutely, to
    its own address, which is no use to a browser out on the domain."""
    return RedirectResponse("http://127.0.0.1:%d/holdem/deeper" % HOLDEM_PORT,
                            status_code=302)


async def missing(request):
    return PlainTextResponse("no such thing", status_code=404)


async def chatty(request):
    """Answers with a header no proxy may forward, plus one it must."""
    return PlainTextResponse("ok", headers={"connection": "keep-alive",
                                            "x-solver": "holdem"})


UPSTREAM = Starlette(routes=[
    Route("/holdem", to_slash),
    Route("/holdem/", page),
    Route("/holdem/gone", missing),
    Route("/holdem/chatty", chatty),
    Route("/holdem/elsewhere", to_itself),
    Route("/holdem/api/echo", echo,
          methods=["GET", "POST", "PUT", "PATCH", "DELETE"]),
])

HOLDEM_PORT = free_port()
DEAD_PORT = free_port()  # nothing is ever bound here, so connecting fails

os.environ["HOLDEM_UPSTREAM"] = "http://127.0.0.1:%d" % HOLDEM_PORT
os.environ["HMRDS_UPSTREAM"] = "http://127.0.0.1:%d" % DEAD_PORT

from fastapi.testclient import TestClient  # noqa: E402

from web import app as site  # noqa: E402  (imported after the env is set)
from web.app import local_location  # noqa: E402

server = uvicorn.Server(uvicorn.Config(UPSTREAM, host="127.0.0.1",
                                       port=HOLDEM_PORT, log_level="error"))


def setUpModule():
    threading.Thread(target=server.run, daemon=True).start()
    deadline = time.monotonic() + 15
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("the stub upstream never came up")
        time.sleep(0.02)


def tearDownModule():
    server.should_exit = True


class ProxyCase(unittest.TestCase):
    """Every case needs the app's lifespan, which is what opens the client."""

    def setUp(self):
        self.client = TestClient(site.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)


class LandingPage(ProxyCase):
    def test_index_is_served_at_the_root(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Alex Wezensky", response.text)

    def test_index_links_to_every_solver(self):
        body = self.client.get("/").text
        for prefix in ("holdem", "noah", "redriver", "hmrds", "prlps"):
            self.assertIn('href="/%s/"' % prefix, body)

    def test_static_assets_are_served(self):
        for name, kind in [("style.css", "text/css"), ("favicon.svg", "image/svg")]:
            with self.subTest(name=name):
                response = self.client.get("/" + name)
                self.assertEqual(response.status_code, 200)
                self.assertIn(kind, response.headers["content-type"])

    def test_health_reports_which_upstreams_are_set(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertEqual(response.json()["upstreams"],
                         {"holdem": True, "hmrds": True, "noah": False,
                          "prlps": False, "redriver": False})

    def test_an_unknown_path_is_not_swallowed_by_the_proxy(self):
        self.assertEqual(self.client.get("/nope").status_code, 404)


class Proxying(ProxyCase):
    def test_upstream_page_comes_back_whole(self):
        response = self.client.get("/holdem/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("upstream", response.text)
        self.assertIn("text/html", response.headers["content-type"])

    def test_the_path_reaches_the_upstream_unrewritten(self):
        body = self.client.get("/holdem/api/echo").json()
        self.assertEqual(body["path"], "/holdem/api/echo")

    def test_query_strings_survive(self):
        body = self.client.get("/holdem/api/echo?mode=exact&trials=5").json()
        self.assertEqual(body["query"], "mode=exact&trials=5")

    def test_a_post_body_survives(self):
        payload = {"hands": ["AsKs", "QhQd"], "board": "Jh Ts 2c"}
        body = self.client.post("/holdem/api/echo", json=payload).json()
        self.assertEqual(json.loads(body["body"]), payload)
        self.assertEqual(body["method"], "POST")

    def test_every_method_is_carried(self):
        for method in ["GET", "POST", "PUT", "PATCH", "DELETE"]:
            with self.subTest(method=method):
                response = self.client.request(method, "/holdem/api/echo")
                self.assertEqual(response.json()["method"], method)

    def test_host_names_the_upstream_and_ours_is_forwarded(self):
        """Railway routes on Host, so it has to name the service being asked;
        the caller's own host travels as x-forwarded-host."""
        body = self.client.get("/holdem/api/echo").json()
        self.assertEqual(body["host"], "127.0.0.1:%d" % HOLDEM_PORT)
        self.assertEqual(body["forwarded_host"], "testserver")
        self.assertEqual(body["proto"], "http")

    def test_hop_by_hop_request_headers_do_not_cross(self):
        """httpx sets `connection` itself for its own leg, so this checks the
        two that only ever arrive because a client sent them."""
        body = self.client.get("/holdem/api/echo", headers={
            "te": "trailers", "upgrade": "websocket", "x-seat": "3",
        }).json()
        self.assertIsNone(body["te"])
        self.assertIsNone(body["upgrade"])
        self.assertEqual(body["custom"], "3", "ordinary headers must still cross")

    def test_hop_by_hop_response_headers_do_not_cross(self):
        response = self.client.get("/holdem/chatty")
        self.assertNotIn("connection", {k.lower() for k in response.headers})
        self.assertEqual(response.headers["x-solver"], "holdem")

    def test_a_redirect_is_handed_back_not_followed(self):
        response = self.client.get("/holdem", follow_redirects=False)
        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "/holdem/")

    def test_an_absolute_redirect_is_brought_back_onto_our_domain(self):
        response = self.client.get("/holdem/elsewhere", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["location"], "/holdem/deeper")

    def test_an_upstream_error_status_is_passed_through(self):
        response = self.client.get("/holdem/gone")
        self.assertEqual(response.status_code, 404)
        self.assertIn("no such thing", response.text)

    def test_content_length_matches_the_body_handed_back(self):
        response = self.client.get("/holdem/")
        self.assertEqual(int(response.headers["content-length"]),
                         len(response.content))


class RewritingRedirects(unittest.TestCase):
    """`local_location` in isolation, including the schemes a real hop mixes."""

    target = "https://solver.up.railway.app"

    def test_a_relative_location_is_left_alone(self):
        self.assertEqual(local_location("/holdem/", self.target), "/holdem/")

    def test_the_upstream_host_is_stripped(self):
        self.assertEqual(
            local_location("https://solver.up.railway.app/holdem/", self.target),
            "/holdem/")

    def test_a_mismatched_scheme_is_still_stripped(self):
        """The upstream answers http when the hop in was plain, https when it
        was not; either way the address is the one we must not hand back."""
        self.assertEqual(
            local_location("http://solver.up.railway.app/holdem/", self.target),
            "/holdem/")

    def test_a_query_string_survives_the_strip(self):
        self.assertEqual(
            local_location("http://solver.up.railway.app/a?b=c", self.target),
            "/a?b=c")

    def test_the_bare_upstream_becomes_our_root(self):
        self.assertEqual(local_location("https://solver.up.railway.app", self.target),
                         "/")

    def test_somewhere_else_entirely_is_left_alone(self):
        other = "https://example.com/elsewhere"
        self.assertEqual(local_location(other, self.target), other)


class MovingDomains(ProxyCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(setattr, site, "CANONICAL_HOST", site.CANONICAL_HOST)
        site.CANONICAL_HOST = "mixedgamesgto.com"

    def get(self, path, host, method="GET"):
        return self.client.request(method, path, headers={"host": host},
                                   follow_redirects=False)

    def test_the_old_domain_moves_to_the_same_page(self):
        for host in ("alexwezensky.com", "www.alexwezensky.com",
                     "www.mixedgamesgto.com"):
            with self.subTest(host=host):
                response = self.get("/holdem/preflopchart?x=1", host)
                self.assertEqual(response.status_code, 308)
                self.assertEqual(response.headers["location"],
                                 "https://mixedgamesgto.com/holdem/preflopchart?x=1")

    def test_a_post_is_moved_as_a_post(self):
        response = self.get("/holdem/api/equity", "alexwezensky.com", "POST")
        self.assertEqual(response.status_code, 308)

    def test_the_new_domain_is_served(self):
        response = self.get("/", "mixedgamesgto.com")
        self.assertEqual(response.status_code, 200)

    def test_other_hosts_are_served_where_they_are(self):
        response = self.get("/api/health", "web-production.up.railway.app")
        self.assertEqual(response.status_code, 200)

    def test_the_old_domain_root_is_the_home_page(self):
        for host in ("alexwezensky.com", "www.alexwezensky.com"):
            with self.subTest(host=host):
                response = self.get("/", host)
                self.assertEqual(response.status_code, 200)
                self.assertIn("qlessdatabase.com", response.text)
                self.assertIn("mixedgamesgto.com", response.text)

    def test_the_new_domain_root_is_still_the_solvers(self):
        response = self.get("/", "mixedgamesgto.com")
        self.assertIn("/holdem/", response.text)
        self.assertNotIn("qlessdatabase.com", response.text)

    def test_nothing_moves_until_a_canonical_host_is_set(self):
        site.CANONICAL_HOST = ""
        response = self.get("/", "alexwezensky.com")
        self.assertEqual(response.status_code, 200)


class Analytics(ProxyCase):
    env = {"GA4_ID": "G-ABC123", "UMAMI_WEBSITE_ID": "1234-abcd",
           "GOATCOUNTER_CODE": "mixedgames"}

    def setUp(self):
        super().setUp()
        self.addCleanup(setattr, site, "ANALYTICS", site.ANALYTICS)
        site.ANALYTICS = site.analytics_snippet(self.env)

    def test_nothing_is_added_without_configuration(self):
        self.assertEqual(site.analytics_snippet({}), "")

    def test_a_malformed_id_is_left_out(self):
        self.assertEqual(site.analytics_snippet({"GA4_ID": "');alert(1)//"}), "")

    def test_goatcounter_is_taken_in_any_shape(self):
        for code in ("mixedgames", "MixedGames", "mixedgames.goatcounter.com",
                     "https://mixedgames.goatcounter.com/",
                     "https://mixedgames.goatcounter.com/count"):
            with self.subTest(code=code):
                self.assertIn("https://mixedgames.goatcounter.com/count",
                              site.analytics_snippet({"GOATCOUNTER_CODE": code}))

    def test_our_pages_carry_all_three(self):
        for path in ("/", "/changelog"):
            with self.subTest(path=path):
                body = self.client.get(path).text
                head = body.split("</head>")[0]
                self.assertIn("gtag/js?id=G-ABC123", head)
                self.assertIn('data-website-id="1234-abcd"', head)
                self.assertIn("https://mixedgames.goatcounter.com/count", head)

    def test_a_solver_page_carries_them_through_the_proxy(self):
        response = self.client.get("/holdem/")
        self.assertIn("G-ABC123", response.text)
        self.assertEqual(int(response.headers["content-length"]),
                         len(response.content))

    def test_json_and_assets_are_untouched(self):
        self.assertNotIn("gtag", self.client.get("/holdem/api/echo").text)
        self.assertNotIn("gtag", self.client.get("/style.css").text)


class WhenAnUpstreamIsMissing(ProxyCase):
    def test_an_unreachable_solver_answers_502(self):
        response = self.client.get("/hmrds/api/health")
        self.assertEqual(response.status_code, 502)
        self.assertIn("unreachable", response.json()["detail"])

    def test_an_unconfigured_solver_answers_503(self):
        site.UPSTREAMS["hmrds"] = ""
        self.addCleanup(site.UPSTREAMS.__setitem__, "hmrds",
                        os.environ["HMRDS_UPSTREAM"])
        response = self.client.get("/hmrds/api/health")
        self.assertEqual(response.status_code, 503)
        self.assertIn("not configured", response.json()["detail"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
