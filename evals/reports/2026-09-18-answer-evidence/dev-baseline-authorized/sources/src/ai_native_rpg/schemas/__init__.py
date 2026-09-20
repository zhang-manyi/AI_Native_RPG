"""Shared Pydantic contracts. This package is the single source of truth for
schemas; ``docs/schemas/`` holds the design-time drafts and may lag behind.

Schemas contain no business logic beyond validation and derived properties, so
that every other layer can depend on them without creating import cycles.
"""
