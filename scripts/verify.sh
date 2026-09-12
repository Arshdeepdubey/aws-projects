#!/usr/bin/env bash
# Static checks across every project. No AWS calls, no credentials, no cost.
#
#   ./scripts/verify.sh            # syntax + structure checks
#   ./scripts/verify.sh --tests    # also run the unit test suites
#
# Optional tools, used when installed: terraform (fmt/validate), cfn-lint,
# pytest, node. Anything missing is reported as skipped, not failed.
set -uo pipefail

cd "$(dirname "$0")/.."
FAILED=0
RUN_TESTS=0
[[ "${1:-}" == "--tests" ]] && RUN_TESTS=1

section() { printf '\n\033[1m%s\033[0m\n' "$1"; }
pass()    { printf '  ok    %s\n' "$1"; }
fail()    { printf '  FAIL  %s\n' "$1"; FAILED=1; }
skip()    { printf '  skip  %s\n' "$1"; }

# ---------------------------------------------------------------- structure
section "Project structure"
for dir in projects/*/; do
  name="$(basename "$dir")"
  missing=()
  [[ -f "$dir/README.md" ]] || missing+=(README.md)
  [[ -f "$dir/Makefile" ]] || missing+=(Makefile)
  if [[ ${#missing[@]} -eq 0 ]]; then pass "$name"; else fail "$name (missing: ${missing[*]})"; fi
done

# ---------------------------------------------------------------- terraform
section "Terraform"
if command -v terraform >/dev/null; then
  for dir in projects/*/infra; do
    [[ -d "$dir" ]] || continue
    ls "$dir"/*.tf >/dev/null 2>&1 || continue

    if terraform fmt -check -recursive "$dir" >/dev/null; then
      pass "fmt $dir"
    else
      fail "fmt $dir (run: terraform fmt -recursive $dir)"
    fi

    if terraform -chdir="$dir" init -backend=false -input=false -no-color >/dev/null 2>&1 \
       && terraform -chdir="$dir" validate -no-color >/dev/null; then
      pass "validate $dir"
    else
      fail "validate $dir"
      terraform -chdir="$dir" validate -no-color 2>&1 | sed 's/^/        /' | head -20
    fi
  done
else
  skip "terraform not installed"
fi

# ------------------------------------------------------------- cloudformation
section "SAM / CloudFormation"
if command -v cfn-lint >/dev/null; then
  for template in projects/*/template.yaml; do
    [[ -f "$template" ]] || continue
    if cfn-lint "$template" >/dev/null; then pass "$template"; else fail "$template"; cfn-lint "$template" | sed 's/^/        /'; fi
  done
else
  skip "cfn-lint not installed (pip install cfn-lint)"
fi

# ------------------------------------------------------------------- python
section "Python syntax"
if python3 -c 'import ast' 2>/dev/null; then
  if python3 - <<'PYEOF'
import ast, pathlib, sys
skip = ('node_modules', '__pycache__', '.pytest_cache', '.venv', '.build')
bad = []
for path in sorted(pathlib.Path('projects').rglob('*.py')):
    if any(part in skip for part in path.parts):
        continue
    try:
        ast.parse(path.read_text(), filename=str(path))
    except SyntaxError as exc:
        bad.append(f"{path}:{exc.lineno}: {exc.msg}")
for line in bad:
    print("        " + line)
sys.exit(1 if bad else 0)
PYEOF
  then pass "all .py files parse"; else fail "python syntax errors"; fi
else
  skip "python3 unavailable"
fi

# --------------------------------------------------------------- javascript
section "JavaScript syntax"
if command -v node >/dev/null; then
  js_failed=0
  while IFS= read -r file; do
    node --check "$file" 2>/dev/null || { fail "$file"; js_failed=1; }
  done < <(find projects -name '*.js' -not -path '*/node_modules/*' -not -path '*/dist/*')
  [[ $js_failed -eq 0 ]] && pass "all .js files parse"
else
  skip "node not installed"
fi

# ------------------------------------------------------------------- shell
section "Shell scripts"
sh_failed=0
while IFS= read -r file; do
  bash -n "$file" || { fail "$file"; sh_failed=1; }
done < <(find projects scripts -name '*.sh')
[[ $sh_failed -eq 0 ]] && pass "all .sh files parse"

# ------------------------------------------------------------- json / yaml
section "JSON and YAML"
if python3 - <<'PYEOF'
import json, pathlib, sys
try:
    import yaml
except ImportError:
    yaml = None

skip = ('node_modules', '__pycache__', '.pytest_cache', 'dist', 'package-lock.json')
bad = []

for path in sorted(pathlib.Path('projects').rglob('*')):
    if not path.is_file() or any(part in skip for part in path.parts) or path.name in skip:
        continue

    if path.suffix in ('.json', '.ipynb'):
        try:
            json.loads(path.read_text())
        except Exception as exc:
            bad.append(f"{path}: {exc}")

    elif path.suffix in ('.yml', '.yaml') and yaml is not None:
        class Loader(yaml.SafeLoader):
            pass
        Loader.add_multi_constructor("!", lambda l, s, n: None)
        try:
            list(yaml.load_all(path.read_text(), Loader=Loader))
        except Exception as exc:
            bad.append(f"{path}: {str(exc).splitlines()[0]}")

for line in bad:
    print("        " + line)
sys.exit(1 if bad else 0)
PYEOF
then pass "all JSON/YAML parses"; else fail "JSON/YAML errors"; fi

# ------------------------------------------------------------------- tests
if [[ $RUN_TESTS -eq 1 ]]; then
  section "Unit tests"

  for dir in projects/*/; do
    if [[ -d "$dir/tests" ]] && command -v pytest >/dev/null; then
      if (cd "$dir" && pytest tests -q >/dev/null 2>&1); then
        pass "pytest $(basename "$dir")"
      else
        fail "pytest $(basename "$dir")"
        (cd "$dir" && pytest tests -q 2>&1 | tail -12 | sed 's/^/        /')
      fi
    fi
  done

  if command -v node >/dev/null; then
    while IFS= read -r pkg; do
      app_dir="$(dirname "$pkg")"
      compgen -G "$app_dir/test/*.test.js" >/dev/null || continue
      if (cd "$app_dir" && node --test test/*.test.js >/dev/null 2>&1); then
        pass "node --test $app_dir"
      else
        fail "node --test $app_dir"
      fi
    done < <(find projects -name package.json -not -path '*/node_modules/*')
  fi
fi

section "Result"
if [[ $FAILED -eq 0 ]]; then
  echo "  everything checked passed"
else
  echo "  some checks failed (see above)"
fi
exit $FAILED
