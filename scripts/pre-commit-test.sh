#!/usr/bin/env bash
# Run python-libei's check suite -- tests, ruff lint, ruff format, mypy -- and
# report a pass/fail summary. Intended as the gate to clear before committing.
#
# The command list mirrors .github/workflows/ci.yml rather than inventing a
# house style, so a green run here means what CI means: the four commands of
# CI's `test` job, in CI's order, over CI's paths. What is deliberately not
# here are the parts of that job which describe the runner rather than the
# tree -- `pip install -e '.[dev]'` and the apt packages -- so run this with
# the dev extra installed in the interpreter it points at.
#
# It does report which of libei/libeis/liboeffis that interpreter can load.
# The integration tests skip themselves when the native libraries are absent,
# which is right on a machine without them; CI asserts at least libei and
# libeis are present, because on a runner a skipped test is a job that proved
# nothing. Here a skip is legitimate, so the libraries are printed rather than
# checked, and the skip count in the summary is worth reading for the same
# reason: a suite that ran with a third of its tests skipped is not the same
# green as one that ran them all.
#
# `--full` adds CI's `build` job, so far as it is checkable here: the sdist and
# wheel built into a temporary directory (CI's `python -m build` fills dist/;
# this writes nothing into the tree), `twine check --strict` over them, and --
# when HEAD is exactly a v* tag -- the tag-matches-the-packaged-version check.
# An interpreter without the build and twine packages reports SKIP, not a pass.
#
# Not covered, deliberately:
#
#   import-without-libei  asserts importing works with *no* native libraries
#                         present -- the property the lazy loader exists to
#                         provide, and one that cannot be reproduced from a
#                         machine where they are installed. CI does it in an
#                         interpreter of its own, with nothing installed.
#   publish               runs on a v* tag and uploads to PyPI. Not a gate.
#
# Note it checks the working tree, not the index. If you have unstaged
# changes, that is not what `git commit` is about to record.
#
# Usage:
#   ./scripts/pre-commit-test.sh             run everything
#   ./scripts/pre-commit-test.sh -k mypy     only checks whose name matches (repeatable)
#   ./scripts/pre-commit-test.sh -v          stream each check's output as it runs
#   ./scripts/pre-commit-test.sh -q          summary only; do not dump failure logs
#   ./scripts/pre-commit-test.sh -x          stop at the first failure
#   ./scripts/pre-commit-test.sh --full      also build the sdist and wheel,
#                                            twine-check them, and check the
#                                            tag when HEAD is one
#
# Exit status: 0 all passed (a SKIP is not a pass, but is not a failure
# either), 1 one or more checks failed, 2 setup problem.

set -uo pipefail

# Repo root, not this script's own directory (scripts/): every check below runs
# `cd "$ROOT"`, and pyproject.toml/src/tests live one level up.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-python3}"
RUFF="${RUFF:-ruff}"

# name|command. Run from the repository root, via eval, so the quoting here is
# ordinary shell quoting. CI's `test` job, in CI's order. -rs so the summary
# names every skip, which is the interesting part of a run on an older libei.
CHECKS=(
    "ruff|$RUFF check src tests"
    "format|$RUFF format --check src tests"
    "mypy|$PYTHON -m mypy"
    "tests|$PYTHON -m pytest -q -rs"
)

# name|command|guard|needs -- only with --full. The guard answers "are the
# tools here?" and is what turns a missing package into a SKIP instead of a
# failure; `needs` is what to name in the SKIP line.
FULL_CHECKS=(
    "build|check_build|needs_build|the build and twine packages"
)

# ---------------------------------------------------------------- arguments

verbose=0
quiet=0
fail_fast=0
full=0
want_checks=()

# The usage text is this script's own header: every comment line from line 2
# down to the first line that is not one. Extracted by pattern rather than by
# line number, so adding a paragraph up there cannot silently truncate --help
# -- or leak the `set` line below into it.
usage() {
    awk 'NR > 1 && !/^#/ { exit } NR > 1 { sub(/^# ?/, ""); print }' "${BASH_SOURCE[0]}"
}

while (($#)); do
    case "$1" in
        -k|--check)   [[ ${2:-} ]] || { echo "-k needs a value" >&2; exit 2; }
                      want_checks+=("$2"); shift 2 ;;
        -v|--verbose) verbose=1; shift ;;
        -q|--quiet)   quiet=1; shift ;;
        -x|--fail-fast) fail_fast=1; shift ;;
        --full)       full=1; shift ;;
        -h|--help)    usage; exit 0 ;;
        *) echo "pre-commit-test.sh: unknown option '$1' (try --help)" >&2; exit 2 ;;
    esac
done

# ------------------------------------------------------------------ display

if [[ -t 1 ]]; then
    TTY=1
    BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[31m'; GREEN=$'\033[32m'
    YELLOW=$'\033[33m'; RESET=$'\033[0m'
else
    TTY=0
    BOLD=; DIM=; RED=; GREEN=; YELLOW=; RESET=
fi

now() { date +%s.%N; }

# ----------------------------------------------------------------- preflight

matches() {  # matches <needle-array-name> <value>; empty array matches all
    local -n _pats="$1"; local val="$2" pat
    ((${#_pats[@]} == 0)) && return 0
    for pat in "${_pats[@]}"; do [[ $val == *"$pat"* ]] && return 0; done
    return 1
}

[[ -f "$ROOT/pyproject.toml" ]] || {
    echo "pre-commit-test.sh: $ROOT does not look like the python-libei repo" >&2
    exit 2
}

missing=()
command -v "$PYTHON" >/dev/null || missing+=("$PYTHON")
command -v "$RUFF" >/dev/null || missing+=("$RUFF")
"$PYTHON" -c 'import pytest' 2>/dev/null || missing+=("pytest (for $PYTHON)")
"$PYTHON" -c 'import mypy' 2>/dev/null || missing+=("mypy (for $PYTHON)")
if ((${#missing[@]})); then
    printf '%spre-commit-test.sh: not installed:%s %s\n' "$RED" "$RESET" "${missing[*]}" >&2
    echo "install with: pip install -e '.[dev]'" >&2
    exit 2
fi

LOGDIR="$(mktemp -d "${TMPDIR:-/tmp}/libei-checks.XXXXXX")"

# Which native libraries the interpreter above can load, for the reason in the
# header. `src` on the path so this answers for the tree as it stands, whether
# or not the package is installed in editable mode -- the same reason CI sets
# PYTHONPATH there. One line per module, so a partial install is visible.
native="$(PYTHONPATH="src${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON" -c '
from libei import ei, eis, oeffis
print(", ".join(m.__name__ + (" yes" if m.is_available() else " no")
                for m in (ei, eis, oeffis)))' 2>/dev/null)" || native=

# --------------------------------------------------------------- full checks

needs_build() { "$PYTHON" -c 'import build, twine' 2>/dev/null; }

# CI's build job, minus the artifact upload. Everything happens in a temporary
# directory rather than in dist/, so a release artifact already sitting in the
# tree is neither clobbered nor mistaken for this run's output.
check_build() {
    local dist status=0
    dist="$(mktemp -d "${TMPDIR:-/tmp}/libei-build.XXXXXX")" || return 1

    # A subshell under `set -e`, so the first failing step is where it stops --
    # the shape CI has, where each step is its own `run:`.
    (
        set -e
        "$PYTHON" -m build --outdir "$dist"
        "$PYTHON" -m twine check --strict "$dist"/*

        # CI's tag check, which only means anything when HEAD *is* a tag. A
        # PyPI version number can never be reused, so a tag disagreeing with
        # the packaged version has to fail rather than ship -- read back out of
        # the built artifact, not out of the source. This is the repo that had
        # most to gain from it: 0.5.2 exists because eleven commits accumulated
        # past v0.5.1, including a signal fix, with nothing anywhere able to
        # notice the released number had stopped describing the tree.
        tagged=""
        if command -v git >/dev/null &&
            tag="$(git -C "$ROOT" describe --exact-match --tags HEAD 2>/dev/null)"; then
            [[ $tag == v* ]] && tagged="$tag"
        fi
        if [[ $tagged ]]; then
            built="$(cd "$dist" && printf '%s' python_libei-*.tar.gz)"
            built="${built#python_libei-}"
            built="${built%.tar.gz}"
            printf 'tag=%s packaged=%s\n' "$tagged" "$built"
            if [[ ${tagged#v} != "$built" ]]; then
                echo "tag $tagged does not match the packaged version $built" \
                    "-- fix the version in pyproject.toml or retag."
                exit 1
            fi
        fi
    )
    status=$?

    rm -rf "$dist"
    return "$status"
}

# --------------------------------------------------------------------- run

results=()   # "check|status|duration|logfile"
failed=0
ran=0
skipped=0
run_start="$(now)"

printf '%s%s%s  %s  %s\n' "$BOLD" "pre-commit-test" "$RESET" \
       "$("$PYTHON" -V 2>&1)" "${DIM}$(date '+%F %T')${RESET}"
printf '\n%s== python-libei ==%s\n' "$BOLD" "$RESET"
printf '  %snative%s   %s\n' "$DIM" "$RESET" \
       "${native:-libei is not importable by $PYTHON -- pip install -e '.[dev]'}"

active_checks=("${CHECKS[@]}")
((full)) && active_checks+=("${FULL_CHECKS[@]}")

for entry in "${active_checks[@]}"; do
    IFS='|' read -r name cmd guard needs <<<"$entry"
    matches want_checks "$name" || continue

    # A guard that says no is a SKIP: printed, counted, and not a failure.
    # --full on a machine without the build packages is a legitimate run, and
    # the summary says which claims it did not check.
    if [[ $guard ]] && ! "$guard"; then
        printf '  %-8s %sSKIP%s  %s(%s not available)%s\n' \
               "$name" "$YELLOW" "$RESET" "$DIM" "$needs" "$RESET"
        results+=("$name|SKIP|-|")
        ((skipped++))
        continue
    fi

    log="$LOGDIR/$name.log"
    if ((verbose)); then
        printf '  %-8s %s$ %s%s\n' "$name" "$DIM" "$cmd" "$RESET"
    elif ((TTY)); then
        printf '  %-8s %s...%s' "$name" "$DIM" "$RESET"
    fi

    start="$(now)"
    if ((verbose)); then
        ( cd "$ROOT" && eval "$cmd" ) 2>&1 | tee "$log"
        status=${PIPESTATUS[0]}
    else
        ( cd "$ROOT" && eval "$cmd" ) >"$log" 2>&1
        status=$?
    fi
    dur="$(awk -v a="$start" -v b="$(now)" 'BEGIN { printf "%.1fs", b - a }')"
    ((ran++))

    cr=$'\r'; { ((verbose)) || ((!TTY)); } && cr=''
    if ((status == 0)); then
        printf '%s  %-8s %sPASS%s  %6s\n' "$cr" "$name" "$GREEN" "$RESET" "$dur"
        results+=("$name|PASS|$dur|$log")
    else
        printf '%s  %-8s %sFAIL%s  %6s  %s(exit %d)%s\n' "$cr" \
               "$name" "$RED" "$RESET" "$dur" "$DIM" "$status" "$RESET"
        results+=("$name|FAIL|$dur|$log")
        ((failed++))
        ((fail_fast)) && break
    fi
done

((ran + skipped)) || { echo "pre-commit-test.sh: no checks matched" >&2; rm -rf "$LOGDIR"; exit 2; }

# ----------------------------------------------------------------- summary

total_dur="$(awk -v a="$run_start" -v b="$(now)" 'BEGIN { printf "%.1fs", b - a }')"

if ((failed && !quiet && !verbose)); then
    for entry in "${results[@]}"; do
        IFS='|' read -r name status dur log <<<"$entry"
        [[ $status == FAIL ]] || continue
        printf '\n%s---- %s ----%s\n' "$YELLOW" "$name" "$RESET"
        # Long test failures are the norm; the tail is where the summary is.
        tail -n 40 "$log"
        printf '%sfull log: %s%s\n' "$DIM" "$log" "$RESET"
    done
fi

# Named rather than counted alone: a skip is --full telling you which claims
# this machine could not check, and a run without them is still a pass.
list_skipped() {
    local entry name status
    for entry in "${results[@]}"; do
        IFS='|' read -r name status _ _ <<<"$entry"
        [[ $status == SKIP ]] && printf '  %sSKIP%s %s\n' "$YELLOW" "$RESET" "$name"
    done
}

printf '\n%s%s%s  ' "$BOLD" "summary" "$RESET"
if ((failed == 0)); then
    printf '%s%d/%d passed%s' "$GREEN" "$ran" "$ran" "$RESET"
    ((skipped)) && printf ', %s%d skipped%s' "$YELLOW" "$skipped" "$RESET"
    printf ' in %s\n' "$total_dur"
    list_skipped
    rm -rf "$LOGDIR"
    exit 0
fi

printf '%s%d of %d failed%s' "$RED" "$failed" "$ran" "$RESET"
((skipped)) && printf ', %s%d skipped%s' "$YELLOW" "$skipped" "$RESET"
printf ' in %s\n' "$total_dur"
list_skipped
for entry in "${results[@]}"; do
    IFS='|' read -r name status dur log <<<"$entry"
    [[ $status == FAIL ]] && printf '  %sFAIL%s %s\n' "$RED" "$RESET" "$name"
done
printf '%slogs kept in %s%s\n' "$DIM" "$LOGDIR" "$RESET"
exit 1