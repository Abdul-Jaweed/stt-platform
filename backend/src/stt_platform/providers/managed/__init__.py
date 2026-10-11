"""Managed (cloud) provider adapters.

Every adapter here speaks the same internal provider contract as the
local engine; nothing outside this package knows which vendor is behind
a provider id. Shared HTTP behaviour (auth, timeouts, error mapping,
size guard, concurrency) lives in ``http_base``.
"""
