"""Integration test fixtures: one ephemeral PostgreSQL per session."""
import pytest


@pytest.fixture(scope="session")
def pg_url():
    from testcontainers.community.postgres import PostgresContainer

    with PostgresContainer("postgres:16") as pg:
        url = pg.get_connection_url()
        url = url.replace("postgresql+psycopg2", "postgresql+psycopg")
        if url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url[len("postgresql://"):]
        yield url
