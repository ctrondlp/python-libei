# python-libei documentation

Python bindings for libei, libeis and liboeffis — the Wayland input-emulation
libraries. For what it is, which module you need and the quick start, begin at
the [project README](../README.md).

## Start here

| File | What's in it |
|------|---------------|
| [install.md](install.md) | The Python package, the native libraries per distribution, the optional `portal` extra, and how to check what actually loaded |
| [getting-started.md](getting-started.md) | Install through a first pointer motion on a real desktop, and the one concept (`frame()`) everything depends on |
| [recipes.md](recipes.md) | Keyboards, touch, absolute positioning, consent persistence, receiver mode, running your own EIS server |
| [troubleshooting.md](troubleshooting.md) | The "when nothing happens" checklist — nearly every failure in libei is silent |

## Reference

| File | What's in it |
|------|---------------|
| [vs-snegg.md](vs-snegg.md) | How this differs from the reference bindings by libei's own author, including two signature issues found by cross-checking against the C source |

## Design and internals

| File | What's in it |
|------|---------------|
| [developers/architecture.md](developers/architecture.md) | The four layers, why `_cobject.py` is the file to read first, and which C functions are deliberately unbound |
| [developers/verification.md](developers/verification.md) | What has actually been driven against real libraries, what has only met a fake, and which libei versions were tested |

Working *on* the package rather than with it:
[CONTRIBUTING.md](../CONTRIBUTING.md).
