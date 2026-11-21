"""Owner 21.11: the WhatsApp MCP server could not start. Commit 37db01d (20.11 16:58) made main.py import the standalone `fastmcp` package
without declaring it in pyproject.toml, and passed `TokenBucketRateLimiter(...)` (the internal bucket) as a middleware, which fails on the
first request with "'TokenBucketRateLimiter' object is not callable". The WhatsApp MCP showed "CONNECTION_CLOSED" in Claude Code and the
release gate stopped at its import check. The server's own healthcheck (uv run python healthcheck.py) is the live proof; these tests keep
the two causes from coming back without needing the network."""
import os
import re

SERVER = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "whatsapp-mcp-server")


def _read(name):
    with open(os.path.join(SERVER, name), encoding="utf-8") as f:
        return f.read()


def test_the_server_declares_every_third_party_package_it_imports():
    main, deps = _read("main.py"), _read("pyproject.toml")
    for pkg, dist in (("fastmcp", "fastmcp"), ("requests", "requests"), ("httpx", "httpx")):
        used = re.search(rf"^\s*(from|import)\s+{pkg}\b", main + _read("whatsapp.py") + _read("extras.py"), re.M)
        if used:
            assert re.search(rf'"{dist}[>=<~ \[]', deps) or f'"{dist}"' in deps, f"{pkg} is imported but not declared in pyproject.toml"


def test_the_rate_limit_is_a_real_middleware():
    # code only, not the comment that explains the old bug
    code = "\n".join(line for line in _read("main.py").splitlines() if not line.strip().startswith("#"))
    assert "RateLimitingMiddleware(" in code, "the rate limit must be a fastmcp middleware"
    assert not re.search(r"middleware\s*=\s*\[\s*TokenBucketRateLimiter", code), \
        "TokenBucketRateLimiter is the internal bucket, not a middleware: the server fails on its first request"
