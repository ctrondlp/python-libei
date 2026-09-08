# Contributing to python-libei

Everything here is about working *on* python-libei. For using it, see
[README.md](README.md); for how the pieces fit together, see
[docs/developers/architecture.md](docs/developers/architecture.md).

## Setting up

```sh
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
```

## Lint, types, tests

```sh
ruff check src tests
python -m mypy

pytest                        # everything; the integration tests below
                              # skip themselves if libei/libeis is absent
pytest -m integration         # only the tests that drive the real libraries
pytest -rs                    # ...and report which tests skipped, and why
```

Tests for a feature the installed libei is too old to provide skip themselves
by checking for the symbol before they negotiate anything —
`tests/conftest.py`'s `requires_symbol()`. Checking up front rather than
catching the failure matters: a capability an older library has never heard of
is accepted silently and simply yields no device, so a test that waited for
one would hang to its timeout instead of skipping.

## Testing against an old libei

CI runs the suite on Python 3.10–3.13 against Ubuntu's libei, which is 1.2.1 —
deliberately older than the 1.6.0 used for development, so the 1.0.0 core
floor and the version gates both get exercised on a real build rather than
only on paper.

To reproduce that locally, build an old libei and point the loader at it:

```sh
git clone --depth 1 --branch 1.2.1 \
    https://gitlab.freedesktop.org/libinput/libei.git
cd libei && meson setup build -Dtests=disabled -Ddocumentation=[] \
    --prefix=$PWD/prefix && ninja -C build install
LD_LIBRARY_PATH=$PWD/prefix/lib64 pytest -q -rs   # from this checkout
```

Expect passes plus skips, never failures or hangs.

A separate job installs the package with no native libraries at all and
imports it, which is the property the lazy loader exists to provide. What has
and has not been verified, and against which libei versions, is in
[docs/developers/verification.md](docs/developers/verification.md).

## Releasing

Versions are SemVer and live in two places — `pyproject.toml` and
`src/libei/__init__.py` — which have to agree with each other and with the
tag. Nothing enforces that yet.

A release is an annotated, `v`-prefixed tag. Pushing it is the whole of it;
PyPI is the only place a release is published, and no GitHub Release is cut:

```sh
git tag -a v0.2.0 -m "0.2.0"
git push origin v0.2.0
```

While the API is unfrozen, the pre-release signal lives in the version itself:
a PEP 440 suffix (`0.2.0a1`) keeps a plain `pip install python-libei` off it,
and a `0.x` version already says the API can move.

Publishing runs from CI on a `v*` tag using PyPI
[Trusted Publishing](https://docs.pypi.org/trusted-publishers/) (OIDC), so
there is no API token in repository secrets to leak or rotate. The `publish`
job in `ci.yml` handles it, uploading the artifacts the `build` job already
ran `twine check` over.

### The configuration outside this repository

That job depends on two pieces of configuration outside this repository, both
of which are in place as of `0.1.0`:

1. On pypi.org, a trusted publisher on the `python-libei` project: owner
   `ctrondlp`, repository `python-libei`, workflow `ci.yml`, environment
   `pypi`. It started life as a **pending** publisher — the flow for a project
   with no releases yet — and the first upload converted it into an ordinary
   project-level one, so a fresh project is the only case that needs the
   pending form again. Every field has to match the workflow exactly; a
   mismatch surfaces as a rejected credential at upload time, not when it is
   saved.
2. A GitHub environment named `pypi`, in the repository settings. A required
   reviewer on it makes each publish a deliberate approval rather than a side
   effect of pushing a tag.

PyPI filenames are immutable, so a bad upload can only be yanked and
superseded by a new version, never replaced — worth rehearsing anything
unusual on TestPyPI first (separate account, separate pending publisher, and
`repository-url: https://test.pypi.org/legacy/` on the publish step).
