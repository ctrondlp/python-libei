# Installing

Two halves, and `pip` only does the first.

## 1. The Python package

```sh
pip install python-libei
```

The distribution is named `python-libei`; the import is `libei`. So `pip show
python-libei`, but `from libei import ei`.

Pure Python, no build step: the wheel is `py3-none-any`, and ctypes talks to
the native libraries directly, so there is no compiler, no headers and no
`libei-devel` involved at install time.

The `portal` extra adds PyGObject, which `libei.portal` needs and the other
three modules do not:

```sh
pip install 'python-libei[portal]'
```

To track `main` instead, or to work on the package itself, install from a
checkout:

```sh
git clone https://github.com/ctrondlp/python-libei.git
cd python-libei
pip install .                    # or: pip install -e '.[dev]' to develop
```

## 2. The native libraries

Nothing on PyPI provides these and `pip` cannot install them. The package
still imports without them — they are loaded on first *use*, not at import
(see `src/libei/_capi/loader.py`) — but every call then raises
`LibraryNotFoundError` naming the symbol it wanted.

| Distribution | Install |
| --- | --- |
| Fedora | `sudo dnf install libei libeis liboeffis` |
| Debian, Ubuntu | `sudo apt install libei1 libeis1 liboeffis1` |
| FreeBSD | `sudo pkg install libei` — the `x11/libei` port, which supplies all three sonames including `liboeffis` |

Those three are the families whose names have actually been checked: Fedora
and FreeBSD from a real install, and Debian from what this repository's own CI
installs. CI deliberately runs against Ubuntu's libei 1.2.1 rather than a
current build, so the 1.0.0 floor is exercised on a genuinely old library.

**On any other distribution the name varies** — the three sonames may be split
across separate packages, or bundled under one. Search rather than assume; on
Arch that is `pacman -Ss libei`.

## 3. Checking what you have

```python
from libei import ei, eis, oeffis

print(ei.is_available(), eis.is_available(), oeffis.is_available())
```

`is_available()` reports whether the *library* loaded, which is not the same
question as whether the import worked — so an application can import this
package, discover the libraries are missing, and fall back to another input
backend without ever handling `ImportError`.

## 4. Which libei version

1.0.0 is the floor, and newer buys specific features rather than a different
API: the full table is in the README's
[Requirements](../README.md#requirements). Because nothing is resolved until
it is called, a library too old for one call costs you that call — it raises
`LibraryNotFoundError` naming the symbol — and not the rest of the package.

## Where to go next

| If you want… | Read |
|---|---|
| A first pointer motion on a real desktop | [getting-started.md](getting-started.md) |
| Something silently does nothing | [troubleshooting.md](troubleshooting.md) |
| Which module to use for what | the README's [Which API do I need?](../README.md#which-api-do-i-need) |
