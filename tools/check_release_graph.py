"""Check the release graph of .github/workflows/release.yml (needs PyYAML).

Usage: python tools/check_release_graph.py [--release PATH] [--build-windows PATH] [--self-test]

--self-test runs the check's reverse cases (SELF_TEST_CASES below): every broken
copy of release.yml must fail and the real one must pass. The lint job runs both.

Runs in test.yml's lint job, i.e. on every push and pull request (the way a
release.yml change reaches main); it does not run inside the release itself.

1. Structure: publish needs check, test-win, build-win, verify-full-win,
   delta-win, verify-delta-win, test-mac, build-mac; build-win / build-mac need
   only check; the verify jobs call verify_update_windows.yml with leg full /
   delta; the check job reads tools/release_flags.json into its no_manifest
   output; publish has a gate step (id gates) running tools/release_gates.py on
   NEEDS_JSON = toJSON(needs) and NO_MANIFEST; publish and the verify / delta
   jobs run on `!cancelled() && ...` (no always()); every step that downloads an
   artifact or uploads / creates / deletes a release asset is one this check
   knows, and the three publishing steps have, in order, the script lines they
   rely on (compared line by line, not as substrings): the full upload takes only
   out/Seraphine.7z / out/Seraphine.dmg, checks them with --check-full-names and
   deletes the version's old delta and manifest before uploading; the manifest /
   delta upload takes only those and checks --check-names; an always() guard after
   it reads the asset names from the REST API and deletes the version's manifest
   and delta unless `release_gates.py --check-first-packages` accepts them (the
   first .7z is Seraphine.7z, the first .dmg Seraphine.dmg);
   build_windows.yml turns its no_manifest input into -NoManifest for the pack
   and --no-manifest for the archive check.
2. Simulation: every combination of the check result, need_win / need_mac, the
   noManifest flag, whether a previous release exists, the ref (main or not),
   whether a hand release attached Seraphine.7z meanwhile, whether the run was
   cancelled by hand after the tests and builds, and success / failure of each of
   the seven work jobs (32,768 scenarios; a failed job keeps its outputs, success()
   covers every ancestor) is pushed
   through the jobs' real `if:` expressions (GitHub's rules: an `if` without a
   status function means `success() && ...`, a job whose condition is false is
   skipped), the gate (tools/release_gates.py itself) and the publish steps' real
   `if:` expressions. What would be uploaded must match the publish rules:
   Seraphine.7z exactly when test-win, build-win and verify-full-win succeeded;
   Seraphine.dmg exactly when test-mac and build-mac did; the manifest asset with
   the Windows package unless noManifest, and only next to a Seraphine.7z this run
   uploaded; the delta with it only when delta-win and verify-delta-win succeeded
   too, never under noManifest; nothing off main; nothing in a cancelled run.
   The builds run whatever the tests do; delta-win never runs under noManifest;
   the verify jobs run exactly when what they verify was built.
Prints one OK line and exits 0, or lists the problems and exits 1.
"""
import argparse
import functools
import importlib.util
import itertools
import os
import re
import sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WORKFLOWS = os.path.join(ROOT, '.github', 'workflows')

PUBLISH_NEEDS = ('check', 'test-win', 'build-win', 'verify-full-win', 'delta-win',
                 'verify-delta-win', 'test-mac', 'build-mac')
WORK_JOBS = ('test-win', 'build-win', 'verify-full-win', 'delta-win', 'verify-delta-win',
             'test-mac', 'build-mac')
VERSION = '9.8.7'
PREV = '9.8.6'
DELTA = 'Seraphine-%s-to-%s.delta' % (PREV, VERSION)
ARTIFACTS = {  # artifact name -> the job that uploads it
    'Seraphine-win-' + VERSION: 'build-win',
    'Seraphine-mac-' + VERSION: 'build-mac',
    'Seraphine-delta-win-' + VERSION: 'delta-win',
}


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


release_gates = _load('release_gates', os.path.join(HERE, 'release_gates.py'))


class GraphError(Exception):
    pass


# ---- GitHub expressions (the subset the workflows use) ----------------------

_TOKEN = re.compile(r"""\s*(?:
    (?P<str>'(?:[^']|'')*')
  | (?P<op>&&|\|\||==|!=|!|\(|\)|,)
  | (?P<word>[A-Za-z_][A-Za-z0-9_-]*(?:\.[A-Za-z_*][A-Za-z0-9_-]*)*)
  )""", re.X)
STATUS_FUNCS = ('always', 'success', 'failure', 'cancelled')


@functools.lru_cache(maxsize=None)
def tokens(text):
    return tuple(_tokens(text))


def _tokens(text):
    out, pos = [], 0
    text = text.strip()
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m or m.end() == pos:
            raise GraphError('cannot parse expression at %r' % text[pos:pos + 30])
        pos = m.end()
        kind = m.lastgroup
        out.append((kind, m.group(kind)))
    return out


def unwrap(expr):
    expr = str(expr).strip()
    if expr.startswith('${{') and expr.endswith('}}'):
        expr = expr[3:-2].strip()
    return expr


def usesStatusFunction(expr):
    return any(kind == 'word' and value in STATUS_FUNCS for kind, value in tokens(unwrap(expr)))


class Evaluator:
    """Evaluates an expression against ``lookup(path)`` and ``funcs[name]()``."""

    def __init__(self, lookup, funcs):
        self.lookup, self.funcs = lookup, funcs

    def run(self, expr):
        self.toks = tokens(unwrap(expr))
        self.i = 0
        value = self.orExpr()
        if self.i != len(self.toks):
            raise GraphError('trailing tokens in %r' % expr)
        return value

    def peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else (None, None)

    def take(self, value=None):
        tok = self.peek()
        if value is not None and tok[1] != value:
            raise GraphError('expected %r, got %r' % (value, tok[1]))
        self.i += 1
        return tok

    def orExpr(self):
        left = self.andExpr()
        while self.peek() == ('op', '||'):
            self.take()
            right = self.andExpr()
            left = left if truthy(left) else right
        return left

    def andExpr(self):
        left = self.cmpExpr()
        while self.peek() == ('op', '&&'):
            self.take()
            right = self.cmpExpr()
            left = right if truthy(left) else left
        return left

    def cmpExpr(self):
        left = self.unary()
        while self.peek() in (('op', '=='), ('op', '!=')):
            op = self.take()[1]
            right = self.unary()
            same = equal(left, right)
            left = same if op == '==' else not same
        return left

    def unary(self):
        if self.peek() == ('op', '!'):
            self.take()
            return not truthy(self.unary())
        return self.primary()

    def primary(self):
        kind, value = self.take()
        if kind == 'str':
            return value[1:-1].replace("''", "'")
        if kind == 'op' and value == '(':
            inner = self.orExpr()
            self.take(')')
            return inner
        if kind == 'word':
            if self.peek() == ('op', '('):
                self.take('(')
                self.take(')')
                if value not in self.funcs:
                    raise GraphError('unsupported function %s()' % value)
                return self.funcs[value]()
            if value in ('true', 'false'):
                return value == 'true'
            if value == 'null':
                return None
            return self.lookup(value)
        raise GraphError('unexpected token %r' % value)


def truthy(v):
    return v not in (False, None, '', 0)


def equal(a, b):
    if isinstance(a, str) and isinstance(b, str):
        return a.casefold() == b.casefold()
    if a is None or b is None:
        return (a or '') == (b or '')
    if type(a) is not type(b):
        raise GraphError('comparing %r with %r (different types)' % (a, b))
    return a == b


def interpolate(text, ev):
    """A string with ${{ }} parts evaluated (a whole-expression value stays typed;
    a YAML literal that is not a string is returned as it is)."""
    if not isinstance(text, str):
        return text
    whole = re.fullmatch(r'\s*\$\{\{(.*)\}\}\s*', text, re.S)
    if whole and '${{' not in whole.group(1):
        return ev.run(whole.group(1))
    return re.sub(r'\$\{\{(.*?)\}\}', lambda m: str(ev.run(m.group(1))), text)


# ---- structure ---------------------------------------------------------------

def needsOf(job):
    n = job.get('needs') or []
    return [n] if isinstance(n, str) else list(n)


def stepRun(step):
    return str(step.get('run') or '')


def classify(step):
    uses = str(step.get('uses') or '')
    run = stepRun(step)
    if uses.startswith('actions/download-artifact'):
        return 'download'
    if 'gh release create' in run:
        return 'full'
    if 'gh release upload' in run:
        return 'extra' if 'filemanifest' in run else 'unknown-upload'
    if 'gh release delete-asset' in run:
        return 'guard' if '--check-first-packages' in run else 'unknown-upload'
    return None


def runLines(step):
    """The run script's lines, stripped, without line continuations and blanks."""
    out = []
    for line in stepRun(step).splitlines():
        line = line.strip()
        if line.endswith('\\'):
            line = line[:-1].rstrip()
        if line and not line.startswith('#'):
            out.append(line)
    return out


def missingInOrder(lines, wanted):
    """The first wanted line (exact, or a prefix when it ends with '...') not found
    after the previous one, else None."""
    i = 0
    for want in wanted:
        prefix = want.endswith('...')
        key = want[:-3] if prefix else want
        while i < len(lines) and not (lines[i].startswith(key) if prefix else lines[i] == key):
            i += 1
        if i == len(lines):
            return want
        i += 1
    return None


ASSETS_REST = 'gh api "repos/$RELEASES_REPO/releases/tags/v$VERSION" --jq'
CLEAN_CASE = 'Seraphine.filemanifest.txt|Seraphine-*-to-"$VERSION".delta)'
DELETE_NAME = 'gh release delete-asset "v$VERSION" "$name" --repo "$RELEASES_REPO" -y'
FULL_LINES = [
    "assets=$(%s '.assets[].name' 2>/dev/null || true)" % ASSETS_REST,
    'for f in out/Seraphine.7z out/Seraphine.dmg; do',
    'python3 tools/release_gates.py --check-full-names "${files[@]}"',
    'for name in $assets; do',
    CLEAN_CASE,
    DELETE_NAME,
    'gh release upload "v$VERSION" "${files[@]}" --repo "$RELEASES_REPO"',
    'gh release create "v$VERSION" "${files[@]}" --repo "$RELEASES_REPO"',
]
EXTRA_LINES = [
    'files=(out/Seraphine.filemanifest.txt)',
    'for d in out/*.delta; do [ -f "$d" ] && files+=("$d"); done',
    'python3 tools/release_gates.py --check-names "${files[@]}"',
    'set +e',
    'gh release upload "v$VERSION" "${files[@]}" --repo "$RELEASES_REPO"',
]
GUARD_LINES = [
    "names=$(%s '.assets[].name' || true)" % ASSETS_REST,
    'if [ "$EXTRA_OUTCOME" != cancelled ] && python3 tools/release_gates.py --check-first-packages $names; then',
    'exit 0',
    'fi',
    "for name in $(%s '.assets[].name' || true); do" % ASSETS_REST,
    CLEAN_CASE,
    DELETE_NAME,
]
GUARD_IF = "always() && steps.extra.outcome != '' && steps.extra.outcome != 'skipped'"

NOT_CANCELLED_JOBS = ('verify-full-win', 'delta-win', 'verify-delta-win', 'publish')


def startsNotCancelled(expr):
    toks = tokens(unwrap(expr)) if expr is not None else ()
    return list(toks[:5]) == [('op', '!'), ('word', 'cancelled'), ('op', '('), ('op', ')'), ('op', '&&')] \
        and ('word', 'always') not in toks


def structure(rel, win):
    problems = []
    jobs = rel.get('jobs') or {}
    for name in PUBLISH_NEEDS + ('publish',):
        if name not in jobs:
            problems.append('release.yml has no job %s' % name)
    if problems:
        return problems
    pub = jobs['publish']
    missing = [j for j in PUBLISH_NEEDS if j not in needsOf(pub)]
    if missing:
        problems.append('publish does not need %s' % ', '.join(missing))
    for b in ('build-win', 'build-mac'):
        if needsOf(jobs[b]) != ['check']:
            problems.append('%s must need only check (got %s)' % (b, needsOf(jobs[b])))
    for job, leg in (('verify-full-win', 'full'), ('verify-delta-win', 'delta')):
        j = jobs[job]
        if not str(j.get('uses') or '').endswith('/verify_update_windows.yml'):
            problems.append('%s does not call verify_update_windows.yml' % job)
        if str((j.get('with') or {}).get('leg')) != leg:
            problems.append('%s does not run leg %s' % (job, leg))
    check = jobs['check']
    if 'steps.flags.outputs.no_manifest' not in str((check.get('outputs') or {}).get('no_manifest')):
        problems.append('the check job has no no_manifest output from its flags step')
    flagSteps = [s for s in check.get('steps') or [] if s.get('id') == 'flags']
    if not flagSteps or 'release_gates.py --flags tools/release_flags.json' not in stepRun(flagSteps[0]):
        problems.append('the check job does not read tools/release_flags.json (step id flags)')
    if 'no_manifest' not in (jobs['build-win'].get('with') or {}):
        problems.append('build-win does not pass no_manifest to build_windows.yml')
    for job in NOT_CANCELLED_JOBS:
        if not startsNotCancelled(jobs[job].get('if')):
            problems.append('%s must run on `!cancelled() && ...` (no always()): a cancelled run publishes nothing'
                            % job)

    steps = pub.get('steps') or []
    gates = [s for s in steps if s.get('id') == 'gates']
    if len(gates) != 1:
        problems.append('publish has no single gate step (id gates)')
    else:
        g = gates[0]
        env = g.get('env') or {}
        if 'tools/release_gates.py' not in stepRun(g) or 'GITHUB_OUTPUT' not in stepRun(g):
            problems.append('the gate step does not write tools/release_gates.py output to GITHUB_OUTPUT')
        if re.sub(r'\s', '', str(env.get('NEEDS_JSON'))) != '${{toJSON(needs)}}':
            problems.append('the gate step NEEDS_JSON is not ${{ toJSON(needs) }}')
        if 'needs.check.outputs.no_manifest' not in str(env.get('NO_MANIFEST')):
            problems.append('the gate step NO_MANIFEST is not the check job no_manifest')
        if g.get('if'):
            problems.append('the gate step must not be conditional')
        if steps.index(g) > min([i for i, s in enumerate(steps) if classify(s)] or [len(steps)]):
            problems.append('the gate step must come before every download / upload step')
    kinds = [classify(s) for s in steps]
    if 'unknown-upload' in kinds:
        problems.append('publish has an upload step this check does not know')
    if kinds.count('full') != 1 or kinds.count('extra') != 1 or kinds.count('guard') != 1:
        problems.append('publish must have one full-package upload, one manifest / delta upload and one '
                        'first-package guard step')
        return problems
    for s, kind in zip(steps, kinds):
        if kind in ('download', 'full', 'extra') and 'steps.gates.outputs.' not in str(s.get('if') or ''):
            problems.append('step %r (%s) is not conditioned on the gate outputs'
                            % (s.get('name') or s.get('uses'), kind))
    full, extra, guard = (steps[kinds.index(k)] for k in ('full', 'extra', 'guard'))
    if full.get('id') != 'full':
        problems.append('the full-package upload step must have id full')
    if extra.get('id') != 'extra':
        problems.append('the manifest / delta upload step must have id extra')
    if not kinds.index('full') < kinds.index('extra') < kinds.index('guard'):
        problems.append('order: full packages, then the manifest / delta, then the first-package guard')
    for what, step, wanted in (('full-package upload', full, FULL_LINES),
                               ('manifest / delta upload', extra, EXTRA_LINES),
                               ('first-package guard', guard, GUARD_LINES)):
        lost = missingInOrder(runLines(step), wanted)
        if lost:
            problems.append('the %s step lacks (in order) the line: %s' % (what, lost))
    fullLines = runLines(full)
    if [ln for ln in fullLines if ln.startswith('for f in') and 'out' in ln] != ['for f in out/Seraphine.7z out/Seraphine.dmg; do']:
        problems.append('the full-package upload must take only out/Seraphine.7z and out/Seraphine.dmg')
    for what, cmd in (('gh release upload', 1), ('gh release create', 1)):
        if sum(1 for ln in fullLines if ln.startswith(what)) != cmd:
            problems.append('the full-package upload must have exactly one `%s` line' % what)
    extraLines = runLines(extra)
    if [ln for ln in extraLines if ln.startswith('files=') or 'files+=' in ln] != EXTRA_LINES[:2]:
        problems.append('the manifest / delta upload must take only the manifest and out/*.delta')
    uploads = [i for i, ln in enumerate(extraLines) if ln.startswith('gh release upload')]
    if len(uploads) != 1 or uploads[0] == 0 or extraLines[uploads[0] - 1] != 'set +e' \
            or any(ln.startswith('gh release create') for ln in extraLines):
        problems.append('the manifest / delta upload must have exactly one `gh release upload` line, '
                        'right after `set +e` (so a failed upload reaches its clean-up)')
    guardLines = runLines(guard)
    if [ln for ln in guardLines if ln.startswith('names=')] != GUARD_LINES[:1] or guardLines[-1:] != ['exit 1']:
        problems.append('the first-package guard must read the asset names from the REST API once and end with exit 1')
    if tokens(unwrap(guard.get('if') or '')) != tokens(GUARD_IF):
        problems.append('the first-package guard must run on exactly `${{ %s }}`' % GUARD_IF)
    if str((guard.get('env') or {}).get('EXTRA_OUTCOME')) != '${{ steps.extra.outcome }}':
        problems.append('the first-package guard must get EXTRA_OUTCOME = ${{ steps.extra.outcome }} '
                        '(a cancelled upload removes the manifest and delta)')

    # build_windows.yml: the flag reaches the pack step and the archive check
    on = win.get('on') or win.get(True) or {}
    for trig in ('workflow_dispatch', 'workflow_call'):
        inp = ((on.get(trig) or {}).get('inputs') or {}).get('no_manifest') or {}
        if inp.get('type') != 'boolean':
            problems.append('build_windows.yml %s has no boolean no_manifest input' % trig)
    bjob = (win.get('jobs') or {}).get('build') or {}
    benv = str((bjob.get('env') or {}).get('NO_MANIFEST'))
    for value, want in ((True, 'true'), (False, 'false')):
        got = interpolate(benv, Evaluator(lambda p, v=value: _inputs(p, v), {}))
        if got != want:
            problems.append('build_windows.yml NO_MANIFEST is %r for no_manifest=%s' % (got, value))
    runs = [stepRun(s) for s in bjob.get('steps') or []]
    if not any("$env:NO_MANIFEST -eq 'true'" in r and '-NoManifest:$noManifest' in r and 'pack_win.ps1' in r
               for r in runs):
        problems.append('build_windows.yml pack step does not pass -NoManifest from NO_MANIFEST')
    if not any("$env:NO_MANIFEST -eq 'true'" in r and '--no-manifest' in r and 'check_archive.py' in r
               for r in runs):
        problems.append('build_windows.yml archive check does not pass --no-manifest from NO_MANIFEST')
    return problems


def _inputs(path, noManifest):
    if path == 'inputs.no_manifest':
        return noManifest
    raise GraphError('unknown context %s' % path)


# ---- simulation ----------------------------------------------------------------

LATE_JOBS = ('verify-full-win', 'delta-win', 'verify-delta-win', 'publish')


def ancestors(jobs, name):
    out, todo = set(), list(needsOf(jobs[name]))
    while todo:
        n = todo.pop()
        if n not in out:
            out.add(n)
            todo.extend(needsOf(jobs.get(n) or {}))
    return out


def jobContext(results, outputs, needs, ref, steps=None, extra=None, outcomes=None):
    def lookup(path):
        parts = path.split('.')
        if parts[0] == 'needs' and len(parts) >= 3:
            job = parts[1]
            if job not in needs:
                return None
            if parts[2] == 'result' and len(parts) == 3:
                return results[job]
            if parts[2] == 'outputs' and len(parts) == 4:
                return (outputs.get(job) or {}).get(parts[3], '')
        if path == 'github.ref':
            return ref
        if parts[0] == 'steps' and steps is not None and len(parts) == 4 and parts[2] == 'outputs':
            return (steps.get(parts[1]) or {}).get(parts[3], '')
        if parts[0] == 'steps' and outcomes is not None and len(parts) == 3 and parts[2] in ('outcome',
                                                                                             'conclusion'):
            return outcomes.get(parts[1], '')
        if extra and path in extra:
            return extra[path]
        raise GraphError('unknown context %s' % path)
    return lookup


def simulate(rel, scenario):
    """(results, published names) of one scenario."""
    jobs = rel['jobs']
    results, outputs = {}, {}
    ref = scenario['ref']
    order = list(jobs)
    done = set()
    while len(done) < len(order):
        progressed = False
        for name in order:
            if name in done or not all(n in done for n in needsOf(jobs[name])):
                continue
            needs = needsOf(jobs[name])
            # cancelled by hand after the tests and builds, before the later jobs
            cancelled = scenario['cancel'] and name in LATE_JOBS
            above = ancestors(jobs, name)
            funcs = {'always': lambda: True,
                     'success': lambda a=above, c=cancelled: not c and all(results[x] == 'success' for x in a),
                     'failure': lambda a=above: any(results[x] == 'failure' for x in a),
                     'cancelled': lambda c=cancelled: c}
            ev = Evaluator(jobContext(results, outputs, needs, ref), funcs)
            cond = jobs[name].get('if')
            if cond is None:
                run = funcs['success']()
            elif usesStatusFunction(cond):
                run = truthy(ev.run(cond))
            else:
                run = funcs['success']() and truthy(ev.run(cond))
            if not run:
                results[name], outputs[name] = 'skipped', {}
            else:
                results[name], outputs[name] = jobOutcome(name, scenario)
                if name == 'build-win':
                    w = jobs[name].get('with') or {}
                    scenario['_buildNoManifest'] = interpolate(w.get('no_manifest'), ev)
            done.add(name)
            progressed = True
        if not progressed:
            raise GraphError('release.yml needs form a cycle or name an unknown job')
    published = set()
    if results.get('publish') == 'success':
        published = publishSteps(rel, results, outputs, scenario)
    return results, published


def jobOutcome(name, s):
    if name == 'check':
        if s['check'] != 'success':
            return 'failure', {}
        return 'success', {'version': VERSION, 'need_win': s['need_win'], 'need_mac': s['need_mac'],
                           'no_manifest': 'true' if s['noManifest'] else 'false'}
    if name == 'publish':
        return 'success', {}
    if name not in WORK_JOBS:
        raise GraphError('unknown job %s in release.yml (teach check_release_graph.py about it)' % name)
    state = s[name]
    out = {}
    if name == 'delta-win':
        # worst case: a failed job's outputs are still set
        out = {'delta': DELTA if s['hasPrev'] else ''}
    return state, out


def publishSteps(rel, results, outputs, s):
    job = rel['jobs']['publish']
    needs = needsOf(job)
    stepOut, files, published, outcomes = {}, set(), set(), {}
    failed = False
    for step in job.get('steps') or []:
        funcs = {'always': lambda: True, 'success': lambda: not failed,
                 'failure': lambda: failed, 'cancelled': lambda: s['cancel']}
        ev = Evaluator(jobContext(results, outputs, needs, s['ref'], stepOut, outcomes=outcomes), funcs)
        cond = step.get('if')
        if cond is None:
            run = not failed
        elif usesStatusFunction(cond):
            run = truthy(ev.run(cond))
        else:
            run = not failed and truthy(ev.run(cond))
        if step.get('id'):
            outcomes[step['id']] = 'success' if run else 'skipped'
        if not run:
            continue
        if step.get('id') == 'gates':
            env = step.get('env') or {}
            try:
                flag = release_gates.parseFlag(str(interpolate(env.get('NO_MANIFEST'), ev)))
            except release_gates.GateError:
                failed = True
                continue
            needsJson = {n: {'result': results[n], 'outputs': outputs.get(n) or {}} for n in needs}
            g = release_gates.gates(needsJson, flag)
            stepOut['gates'] = {k: 'true' if v else 'false' for k, v in g.items()}
            continue
        kind = classify(step)
        if kind == 'download':
            name = str(interpolate((step.get('with') or {}).get('name'), ev))
            producer = ARTIFACTS.get(name)
            if producer is None:
                raise GraphError('publish downloads an unknown artifact %s' % name)
            if results.get(producer) != 'success':
                failed = True  # the artifact does not exist
                continue
            if producer == 'build-win':
                files.add('Seraphine.7z')
                if s.get('_buildNoManifest') is False:
                    files.add('Seraphine.filemanifest.txt')
            elif producer == 'build-mac':
                files.add('Seraphine.dmg')
            elif outputs['delta-win'].get('delta'):
                files.add(outputs['delta-win']['delta'])
        elif kind == 'full':
            # a hand release may have attached Seraphine.7z meanwhile: not overwritten
            up = {f for f in ('Seraphine.7z', 'Seraphine.dmg') if f in files
                  and not (f == 'Seraphine.7z' and s['handWin'])}
            published |= up
            stepOut[step.get('id') or '_full'] = {'uploaded_win': 'true' if 'Seraphine.7z' in up else 'false'}
        elif kind == 'extra':
            env = step.get('env') or {}
            if 'Seraphine.filemanifest.txt' not in files:
                failed = True  # the step exits 1 without a manifest
                outcomes[step.get('id') or '_extra'] = 'failure'
                continue
            published.add('Seraphine.filemanifest.txt')
            if str(interpolate(env.get('PUBLISH_DELTA', 'false'), ev)) == 'true':
                published |= {f for f in files if f.endswith('.delta')}
    return published


def oracle(s, results, published):
    """Problems of one scenario against the publish rules."""
    ok = {j: s[j] == 'success' for j in WORK_JOBS}
    base = s['check'] == 'success' and s['ref'] == 'refs/heads/main' and not s['cancel']
    win = (base and s['need_win'] == 'true' and ok['test-win'] and ok['build-win'] and ok['verify-full-win']
           and not s['handWin'])
    mac = base and s['need_mac'] == 'true' and ok['test-mac'] and ok['build-mac']
    manifest = win and not s['noManifest']
    delta = manifest and s['hasPrev'] and ok['delta-win'] and ok['verify-delta-win']
    want = set()
    if win:
        want.add('Seraphine.7z')
    if mac:
        want.add('Seraphine.dmg')
    if manifest:
        want.add('Seraphine.filemanifest.txt')
    if delta:
        want.add(DELTA)
    problems = []
    if published != want:
        problems.append('publishes %s, the rules say %s' % (sorted(published), sorted(want)))
    ran = {j: results.get(j) not in ('skipped', None) for j in WORK_JOBS}
    checked = s['check'] == 'success'
    builtWin = checked and s['need_win'] == 'true'
    if ran['build-win'] != builtWin:
        problems.append('build-win %s (it must run whenever check succeeded and Windows is needed, '
                        'whatever the tests do)' % ('ran' if ran['build-win'] else 'was skipped'))
    if ran['build-mac'] != (checked and s['need_mac'] == 'true'):
        problems.append('build-mac %s' % ('ran' if ran['build-mac'] else 'was skipped'))
    late = not s['cancel']
    if ran['verify-full-win'] != (builtWin and ok['build-win'] and late):
        problems.append('verify-full-win %s' % ('ran' if ran['verify-full-win'] else 'was skipped'))
    deltaRuns = builtWin and ok['build-win'] and not s['noManifest'] and late
    if results.get('publish') not in ('skipped', None) and s['cancel']:
        problems.append('publish ran in a cancelled run')
    if ran['delta-win'] != deltaRuns:
        problems.append('delta-win %s (noManifest=%s)' % ('ran' if ran['delta-win'] else 'was skipped',
                                                           s['noManifest']))
    if ran['verify-delta-win'] != (deltaRuns and ok['delta-win'] and s['hasPrev']):
        problems.append('verify-delta-win %s' % ('ran' if ran['verify-delta-win'] else 'was skipped'))
    if ran['build-win'] and s.get('_buildNoManifest') is not s['noManifest']:
        problems.append('build-win gets no_manifest=%r for noManifest=%s'
                        % (s.get('_buildNoManifest'), s['noManifest']))
    return problems


def scenarios():
    dims = [('check', ('success', 'failure')), ('need_win', ('true', 'false')),
            ('need_mac', ('true', 'false')), ('noManifest', (True, False)), ('hasPrev', (True, False)),
            ('ref', ('refs/heads/main', 'refs/heads/feature')), ('handWin', (False, True)),
            ('cancel', (False, True))]
    dims += [(j, ('success', 'failure')) for j in WORK_JOBS]
    keys = [k for k, _ in dims]
    for values in itertools.product(*[v for _, v in dims]):
        yield dict(zip(keys, values))


def check(releasePath, buildWindowsPath):
    with open(releasePath, encoding='utf-8') as f:
        rel = yaml.safe_load(f)
    with open(buildWindowsPath, encoding='utf-8') as f:
        win = yaml.safe_load(f)
    problems = structure(rel, win)
    if any(p.startswith('release.yml has no job') for p in problems):
        return problems, 0
    count = 0
    seen = set()
    for s in scenarios():
        count += 1
        results, published = simulate(rel, s)
        for p in oracle(s, results, published):
            if p not in seen:
                seen.add(p)
                problems.append('%s [scenario %s]' % (p, ', '.join(
                    '%s=%s' % (k, v) for k, v in s.items() if not k.startswith('_'))))
        if len(problems) >= 12:
            break
    return problems, count


# ---- self-test: the reverse cases ---------------------------------------------
# Each case breaks one thing in a copy of release.yml: (old text, new text, a
# fragment of the problem the check must report). The check must exit 1 on every
# copy and pass on the unmodified file.
#   A  verify-full-win dropped from publish needs
#   B  the full-package upload takes every file in out/ (`for f in out/*`)
#   C  test-win dropped from the Windows download (build + verify results instead
#      of the gate); C2 the gate kept but `|| build-win succeeded` added
#   G  no_manifest no longer reaches delta-win's `if`
#   J  the manifest / delta upload no longer requires that this run uploaded Seraphine.7z
#   K  the first-package guard uses a constant instead of the REST API read
#   L  publish on always() again (a cancelled run would publish); L2 verify-full-win
#   M  the first-package guard step removed
#   N  build-win waits for test-win again
#   O  the full-package upload no longer deletes the version's old delta / manifest
#   P  the full-package upload checks names with --check-names (accepts a delta)
#   Q  build-win gets a constant no_manifest
#   X1 the guard's `if` narrowed to a failed upload only; X9 to `!= 'skipped'` only
#   X3 `set +e` removed before the manifest / delta upload
#   X5 a second `gh release upload` (of out/*.delta) in the full-package upload
#   R  the guard's read of the asset names without `|| true` (a failed read would skip the clean-up)
#   S  the guard ignores a cancelled upload (EXTRA_OUTCOME not checked)
_WIN_DOWNLOAD = """      - if: steps.gates.outputs.publish_win == 'true'
        uses: actions/download-artifact@v4"""
SELF_TEST_CASES = {
    'A': ('needs: [check, test-win, build-win, verify-full-win, delta-win,', 'needs: [check, test-win, build-win, delta-win,',
          'publish does not need verify-full-win'),
    'B': ('for f in out/Seraphine.7z out/Seraphine.dmg; do', 'for f in out/*; do',
          'the full-package upload'),
    'C': (_WIN_DOWNLOAD, _WIN_DOWNLOAD.replace("steps.gates.outputs.publish_win == 'true'",
                                             "needs.build-win.result == 'success' && needs.verify-full-win.result == 'success'"),
          'publishes'),
    'C2': (_WIN_DOWNLOAD, _WIN_DOWNLOAD.replace("== 'true'", "== 'true' || needs.build-win.result == 'success'"),
           "publishes ['Seraphine.7z'"),
    'G': ("needs.build-win.result == 'success' && needs.check.outputs.no_manifest == 'false' }}",
          "needs.build-win.result == 'success' }}", 'delta-win ran (noManifest=True)'),
    'J': ("if: steps.full.outputs.uploaded_win == 'true' && steps.gates.outputs.publish_manifest == 'true'",
          "if: steps.gates.outputs.publish_manifest == 'true'", 'publishes'),
    'K': ("""names=$(gh api "repos/$RELEASES_REPO/releases/tags/v$VERSION" --jq '.assets[].name' || true)""",
          'names=Seraphine.7z', 'first-package guard'),
    'L': ("if: ${{ !cancelled() && needs.check.result == 'success' && github.ref == 'refs/heads/main' }}",
          "if: always() && needs.check.result == 'success' && github.ref == 'refs/heads/main'",
          'publish must run on `!cancelled() && ...`'),
    'L2': ("if: ${{ !cancelled() && needs.build-win.result == 'success' }}",
           "if: always() && needs.build-win.result == 'success'",
           'verify-full-win must run on `!cancelled() && ...`'),
    'M': ("""      - name: Check that the first .7z / .dmg asset is the full package
        if: ${{ always() && steps.extra.outcome != '' && steps.extra.outcome != 'skipped' }}""",
          """      - name: Check that the first .7z / .dmg asset is the full package
        if: ${{ false }}
        run: echo none
      - name: unused
        if: ${{ false }}""", 'first-package guard'),
    'N': ("""  build-win:
    needs: [check]""", """  build-win:
    needs: [check, test-win]""", 'build-win must need only check'),
    'O': ('''                    Seraphine.filemanifest.txt|Seraphine-*-to-"$VERSION".delta)
                      gh release delete-asset "v$VERSION" "$name" --repo "$RELEASES_REPO" -y
                      echo "::notice::deleted $name (it belonged to an older Seraphine.7z)"''',
          '''                    none)
                      echo "::notice::kept $name"''', 'the full-package upload step lacks'),
    'P': ('python3 tools/release_gates.py --check-full-names "${files[@]}"',
          'python3 tools/release_gates.py --check-names "${files[@]}"', '--check-full-names'),
    'Q': ("no_manifest: ${{ needs.check.outputs.no_manifest == 'true' }}", 'no_manifest: false',
          'build-win gets no_manifest=False'),
    'X1': ("if: ${{ always() && steps.extra.outcome != '' && steps.extra.outcome != 'skipped' }}",
           "if: ${{ always() && steps.extra.outcome == 'failure' }}", 'the first-package guard must run on exactly'),
    'X9': ("if: ${{ always() && steps.extra.outcome != '' && steps.extra.outcome != 'skipped' }}",
           "if: ${{ always() && steps.extra.outcome != 'skipped' }}", 'the first-package guard must run on exactly'),
    'X3': ('''          set +e
          gh release upload "v$VERSION" "${files[@]}" --repo "$RELEASES_REPO"
          rc=$?''', '''          gh release upload "v$VERSION" "${files[@]}" --repo "$RELEASES_REPO"
          rc=$?''', 'right after `set +e`'),
    'X5': ('''              gh release upload "v$VERSION" "${files[@]}" --repo "$RELEASES_REPO"
              echo "::notice::uploaded ${files[*]} onto v$VERSION in $RELEASES_REPO"''',
           '''              gh release upload "v$VERSION" "${files[@]}" --repo "$RELEASES_REPO"
              gh release upload "v$VERSION" out/*.delta --repo "$RELEASES_REPO"
              echo "::notice::uploaded ${files[*]} onto v$VERSION in $RELEASES_REPO"''',
           'exactly one `gh release upload` line'),
    'R': ('''names=$(gh api "repos/$RELEASES_REPO/releases/tags/v$VERSION" --jq '.assets[].name' || true)''',
          '''names=$(gh api "repos/$RELEASES_REPO/releases/tags/v$VERSION" --jq '.assets[].name')''', 'first-package guard'),
    'S': ('''          if [ "$EXTRA_OUTCOME" != cancelled ] && python3 tools/release_gates.py --check-first-packages $names; then''',
          '''          if python3 tools/release_gates.py --check-first-packages $names; then''', 'first-package guard'),
}


def selfTest(releasePath, buildWindowsPath):
    """Problems of the reverse cases (empty when every break is caught)."""
    import tempfile
    with open(releasePath, encoding='utf-8') as f:
        text = f.read()
    problems, count = check(releasePath, buildWindowsPath)
    if problems:
        return ['the unmodified release.yml fails: %s' % problems[0]]
    out = []
    with tempfile.TemporaryDirectory() as d:
        for name, (old, new, why) in SELF_TEST_CASES.items():
            if text.count(old) != 1:
                out.append('case %s no longer applies to release.yml (update SELF_TEST_CASES)' % name)
                continue
            path = os.path.join(d, 'release_%s.yml' % name)
            with open(path, 'w', encoding='utf-8') as f:
                f.write(text.replace(old, new))
            try:
                found, _ = check(path, buildWindowsPath)
            except (GraphError, yaml.YAMLError) as e:
                found = ['%s: %s' % (type(e).__name__, e)]
            if not found:
                out.append('case %s: the broken copy PASSED the check' % name)
            elif not any(why in prob for prob in found):
                out.append('case %s: caught, but not as %r (got %r)' % (name, why, found[0][:160]))
            else:
                print('check_release_graph: self-test case %s caught: %s' % (name, found[0][:110]))
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--release', default=os.path.join(WORKFLOWS, 'release.yml'))
    p.add_argument('--build-windows', default=os.path.join(WORKFLOWS, 'build_windows.yml'))
    p.add_argument('--self-test', action='store_true',
                   help='run the reverse cases: every broken copy of release.yml must fail')
    a = p.parse_args(argv)
    if a.self_test:
        try:
            bad = selfTest(a.release, a.build_windows)
        except (GraphError, OSError, yaml.YAMLError) as e:
            bad = ['%s: %s' % (type(e).__name__, e)]
        for prob in bad:
            print('check_release_graph: self-test: %s' % prob, file=sys.stderr)
        if bad:
            print('check_release_graph: self-test FAILED', file=sys.stderr)
            return 1
        print('check_release_graph: self-test OK: all %d broken copies of release.yml fail, the real one passes'
              % len(SELF_TEST_CASES))
        return 0
    try:
        problems, count = check(a.release, a.build_windows)
    except (GraphError, OSError, yaml.YAMLError) as e:
        print('check_release_graph: FAILED: %s' % e, file=sys.stderr)
        return 1
    if problems:
        for prob in problems:
            print('check_release_graph: %s' % prob, file=sys.stderr)
        print('check_release_graph: FAILED (%d problems)' % len(problems), file=sys.stderr)
        return 1
    print('check_release_graph: OK: structure, %d scenarios simulated against the publish rules' % count)
    return 0


if __name__ == '__main__':
    sys.exit(main())
