"""Shared pytest fixtures (extended by later tasks: audio fixtures, PG container)."""
import os

# Docker Desktop on Windows: testcontainers must reach containers over IPv4
# loopback; ::1 hangs. Must be set before the docker client is constructed.
os.environ.setdefault("TESTCONTAINERS_HOST_OVERRIDE", "127.0.0.1")
