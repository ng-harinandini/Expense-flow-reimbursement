"""Domain layer — business rules that hold regardless of transport or storage.

Contains the claim state machine, domain validation, and the domain error hierarchy.
This package must not import FastAPI or SQLAlchemy session/engine machinery: it depends only
on the ORM models (as plain data holders) and the standard library.
"""
