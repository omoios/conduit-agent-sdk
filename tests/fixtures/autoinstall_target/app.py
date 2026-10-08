"""Fixture app: a greeting library.

The setup goal is to make `python -c 'import app; app.greet()'` print
'hello from autoinstall-target'.
"""


def greet() -> str:
    return "hello from autoinstall-target"


if __name__ == "__main__":
    print(greet())
