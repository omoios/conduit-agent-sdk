"""Pytest configuration for the conduit-agent-sdk test suite.

``tests/fixtures/`` holds in-process fixtures (e.g. the buggy-calculator
repo loaded via importlib by the scripted-fixer test), NOT pytest test
modules — exclude it from collection.
"""

collect_ignore = ["fixtures"]
