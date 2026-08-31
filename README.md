# alexwezensky.com

Landing page for the poker solvers, and the front door they sit behind.

Railway points one domain at one service, so the two solvers cannot each own
`alexwezensky.com` on their own. This service does: it serves the landing page
at `/` and forwards anything under `/holdem` or `/hmrs` to the service that
owns that prefix, handing back whatever comes out. Both solvers already mount
themselves under those exact prefixes, so no path is rewritten on the way
through and every existing link keeps working.

```
                     alexwezensky.com
                            |
                    [ this service ]
                     /      |      \
                  /       /holdem   /hmrs
             index.html     |          |
                     Hold'em solver  HMRS solver
```

## Configuration

| Variable | Meaning |
| --- | --- |
| `HOLDEM_UPSTREAM` | Base URL of the Hold'em service, e.g. `https://holdem-production.up.railway.app` |
| `HMRS_UPSTREAM` | Base URL of the HMRS service |
| `PORT` | Set by Railway; defaults to 8080 |

Give each one the solver's own Railway domain, with no trailing slash. A prefix
left unset still routes; it answers `503` with a plain english reason rather
than leaving a dead link on the landing page.

The `Host` header sent upstream names the solver rather than this site, because
Railway's edge reads it to decide which service a request belongs to --
forwarding our own would either miss the solver or route straight back here.
The caller's host travels as `X-Forwarded-Host`, and any redirect that comes
back naming the upstream is rewritten onto our domain before the browser sees
it.

### Why not the private network

Railway's private network is IPv6 only and its public network is IPv4 only, so
a service reachable on both has to dual stack bind. Uvicorn does not: given
`--host ::` it listens on IPv6 alone and drops off the public internet, and
given `--host 0.0.0.0` it never appears on the private network. Both solvers
run under Uvicorn and both still want their own public domains, so this proxy
talks to them the way anyone else would.

Moving to the private network is a fair thing to want -- it would keep the
solvers off the public internet entirely -- but it means changing what serves
them, to Hypercorn or to Gunicorn with the Uvicorn worker, either of which does
dual stack bind. The upstream URLs would then become

```
HOLDEM_UPSTREAM = http://${{Hold em.RAILWAY_PRIVATE_DOMAIN}}:8080
```

and nothing in this service would have to change.

## Running it locally

```bash
pip install -r requirements.txt
HOLDEM_UPSTREAM=http://127.0.0.1:8001 HMRS_UPSTREAM=http://127.0.0.1:8002 \
  uvicorn web.app:app --reload --port 8000
```

with each solver running beside it:

```bash
cd ../Poker-Solvers/"Hold 'em" && uvicorn web.app:app --port 8001
cd ../Poker-Solvers/Hemorrhoids && uvicorn web.app:app --port 8002
```

Then open http://127.0.0.1:8000.

`GET /api/health` is the liveness probe, and reports which upstreams are set.

## Tests

```bash
python tests/test_site.py
```

Stands up a real upstream on a real socket and checks that the landing page is
served, that methods, bodies, query strings and status codes survive the round
trip, that `Host` names the upstream while ours travels as `X-Forwarded-Host`,
that redirects are handed back rather than followed and absolute ones are
rewritten onto our domain, that hop-by-hop headers are dropped in both
directions, and that an upstream which is down or unset fails with a readable
message instead of a stack trace.
