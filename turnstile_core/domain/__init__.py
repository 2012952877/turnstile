"""Contracts and pure logic. Depends on nothing else in this package.

Everything here is either a Pydantic model or a function over plain values, so it can be
imported from any layer without creating a cycle. `anomaly_engine` lives here rather than
in `services` for exactly that reason: `persistence.repository` evaluates rules while
reading, and a service-layer import would point upwards.
"""
