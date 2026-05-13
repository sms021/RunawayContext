"""OTLP exporter stub — opt-in network module (HR-1).

This module is intentionally empty in the reference implementation. It is
allowlisted in ``test_no_network_imports`` so that, once implemented, it
may import ``urllib``/``http``/``requests`` to ship events to a user-configured
collector. The default install never loads this module; activating it requires
explicit user configuration.

Spec: ``docs/specs/OTLP_EXPORT.md``.
"""
