---
name: release
description: Cut a Walk the dog release end to end. Merges a work branch into main, bumps the manifest version and dates the CHANGELOG section, runs every pre-release check, commits and pushes, waits for CI, tags with scripts/release.py, then verifies the published GitHub release that HACS offers. Use only when the user asks to release, publish or tag a version.
argument-hint: "[patch | minor | major | X.Y.Z]"
disable-model-invocation: true
---

# Release Walk the dog

Publish version `$ARGUMENTS` of the integration. With no argument, work the bump out from the
`## [Unreleased]` section, as described in step 3.

A pushed tag publishes a GitHub release, and HACS offers it to every user within the hour.
Nothing after step 8 can be quietly undone. So every gate below is a hard stop: when a check
fails, fix the cause or report it to the user, and never tag around it.

The rules this follows live in `CLAUDE.md` (workflow, commits, secrets) and
`docs/DEVELOPMENT.md` § Releasing. If either says something different from this file, they
win. Tell the user about the mismatch so this skill can be corrected.

## 1. Read the state before changing anything

Run these and read every result:

```
git fetch --tags origin
git status -sb
git branch --show-current
git log --oneline origin/main -5
python scripts/release.py --print-version
git tag -l "v*" --sort=-v:refname
gh auth status
```

Then work out which of these cases you are in:

- **Normal release.** The manifest version already has a tag, and `[Unreleased]` has entries.
  Run every step.
- **Prepared but untagged.** The manifest version has a dated CHANGELOG section and no tag
  yet. Skip step 3, and still run every check.
- **Nothing to release.** `[Unreleased]` is empty and the manifest version is already tagged.
  Stop and tell the user.

Stop and ask the user when:

- `gh` is not logged in. You need it to watch the workflows.
- The local `main` has diverged from `origin/main`. Do not rebase or force anything.
- The working tree has changes you did not make in this session. Ask whether they belong in
  the release. Never stash or discard them yourself.

## 2. Merge the work branch

Work normally happens on `main`. If the current branch is something else:

1. Make sure the branch is committed and clean.
2. Run `git checkout main`, then `git pull --ff-only origin main`.
3. Run `git merge --ff-only <branch>`. If a fast-forward is impossible, run a normal
   `git merge --no-edit <branch>`.
4. On a conflict, run `git merge --abort` and stop. Hand the conflict to the user. Do not
   resolve it inside a release.
5. Keep the branch. Deleting it is the user's decision.

If you are already on `main`, run `git pull --ff-only origin main` and continue.

## 3. Choose the version and write it down

Pick the version by SemVer from what `[Unreleased]` actually says:

| The section contains | Bump |
|---|---|
| A removed or renamed option, entity ID or event, or a change that needs users to reconfigure | major |
| `### Added`, or any new user-visible option or behaviour | minor |
| Only `### Fixed`, or internal `### Changed` with no visible effect | patch |

An explicit argument wins. If the argument disagrees with the table, say so in one sentence
and use the argument. If the section is ambiguous between two levels, ask the user.

Then edit these files:

1. **`custom_components/walk_the_dog/manifest.json`.** Set `version`. This is the only place
   the version is defined.
2. **`CHANGELOG.md`.**
   - Rename `## [Unreleased]` to `## [X.Y.Z] - YYYY-MM-DD`, using today's date.
   - Add a fresh, empty `## [Unreleased]` above it.
   - At the foot of the file, point `[Unreleased]` at `compare/vX.Y.Z...HEAD`.
   - Add `[X.Y.Z]: https://github.com/zyndata/walk-the-dog/releases/tag/vX.Y.Z` above the
     previous version's link.
3. **Other places that name the current version.** Search for the previous version number:

   ```
   git grep -n "<previous X.Y.Z>" -- README.md info.md docs
   ```

   Update a mention that states the *current* version. For example, README § Maturity says
   which version "is feature-complete". Leave historical mentions alone, such as
   "fixed in 1.2.1".

The CHANGELOG section becomes the release page word for word. Read it as a user would.
Entries must be in plain language, say what the user notices, and name no internal functions.
Rewrite anything that reads like a commit message.

## 4. Run the checks

Every check must pass. Record what you ran and the results, because step 6 needs them.

1. **The metadata agrees.**

   ```
   python scripts/release.py
   ```

2. **Lint and format are clean.**

   ```
   python scripts/lint.py
   ```

3. **The whole test suite passes offline.** Pick the command for the machine you are on:
   - Linux: `python scripts/test.py`
   - Windows: pytest cannot run natively, because Home Assistant imports `fcntl`. Use the
     local `wtd-test` Docker image. Copy the tree without `.venv`, or the copy alone takes
     more than ten minutes:

     ```
     docker run --rm --network none -v "<repo root>:/src:ro" wtd-test sh -c "mkdir /repo2 && cd /src && tar --exclude=./.venv --exclude=./.git --exclude=./.pytest_cache --exclude=./.ruff_cache -cf - . | tar -C /repo2 -xf - && cd /repo2 && python -m pytest -q -p no:cacheprovider"
     ```

     If the image is missing, stop and point the user to `docs/DEVELOPMENT.md` § Tests do not
     run natively on Windows. Do not release on "the tests passed last time".

   The suite includes `tests/test_release.py`, `tests/test_strings.py` and
   `tests/test_syntax_floor.py`. These cover version order, translations and the Python 3.13
   floor, so a green suite covers those checks too.

4. **No secrets or personal data since the last release.** The repository is public, along
   with its whole history.

   ```
   git diff <previous tag>..HEAD
   git diff <previous tag>..HEAD | grep -niE "api[_-]?key|secret|token|password|bearer|BEGIN [A-Z ]*PRIVATE KEY|homeassistant\.local|:8123|https?://(192\.168|10\.|172\.(1[6-9]|2[0-9]|3[01]))|@[a-z0-9-]+\.(com|pl|net|org)|5[0-5]\.[0-9]{4,}|1[4-9]\.[0-9]{4,}|2[0-4]\.[0-9]{4,}"
   ```

   Judge each hit. Named city-centre coordinates in tests and docs are fine, for example
   Warszawa 52.2297, 21.0122. Stop on anything that looks like a real home location, a Home
   Assistant URL, a token, or someone's email address. The `noreply` attribution addresses in
   commits are fine. Also check that `.env` and `.claude/settings.local.json` are not tracked:

   ```
   git ls-files .env .claude/settings.local.json
   ```

   That command must print nothing.

5. **Validation is green on the base.** Local tools cannot run hassfest or the HACS check,
   so confirm that **CI** and **Validate** succeeded on the commit you are releasing from:

   ```
   gh run list --branch main --limit 6
   ```

   A red Validate run on the base stops the release, even if it is older.

## 5. Review the diff you are about to commit

Run `git diff` and `git status`. The release commit must contain only the version bump, the
CHANGELOG change, the version mentions from step 3.3 and the `STATE.md` entry from step 6.
Commit any other work in its own conventional commit first, and only with the user's consent
when you did not write that work yourself.

## 6. Record the release in STATE.md

`STATE.md` is the handover between machines. Append an entry in the style of the earlier
release entries:

- **Heading:** `## Release X.Y.Z — <one-line theme> (YYYY-MM-DD, out of phase)`. Leave out
  "out of phase" when the release closes a `PLAN.md` phase.
- **Status and date.**
- **What the release contains.** Summarise the CHANGELOG section and link the commits it
  covers.
- **Checks run.** Give the test count, lint result, secret scan result and base CI result.
- **Decisions.** Include the bump level and why.
- **Open questions carried forward.**

## 7. Commit and push, then wait for CI

1. Stage the files explicitly by name. Do not use `git add -A`.
2. Commit with a Conventional Commit message and end it with the attribution trailer this
   session prescribes:

   ```
   chore(release): X.Y.Z
   ```

   Add a short body that names the headline changes.
3. Run `git push origin main`.
4. Wait for **CI** and **Validate** to finish on the new commit. Use a Bash command that exits
   when both runs are complete, rather than polling by hand:

   ```
   gh run list --commit "$(git rev-parse HEAD)" --json name,status,conclusion
   ```

   `--commit` matches only the full 40-character hash. A short hash prints nothing, which
   looks like "no runs yet" forever.

   Both runs must conclude `success`. If either fails, stop. Fix the cause in a new commit and
   repeat this step. The tag must point at a commit that CI has checked.

## 8. Tag

```
python scripts/release.py --tag
```

The script checks the metadata again. It refuses a dirty tree or an existing tag, creates the
annotated tag `vX.Y.Z` and pushes it. Do not create or push the tag by hand. If the script
refuses, report why and stop.

## 9. Verify the published release

1. Wait for the **Release** workflow run on the tag to finish, using the same kind of
   exit-when-done command as in step 7.
2. Check the release:

   ```
   gh release view vX.Y.Z --json tagName,name,isDraft,isPrerelease,url,body
   ```

   All of the following must hold:
   - `isDraft` is false.
   - `isPrerelease` is false. HACS hides pre-releases from most users.
   - `name` is `Walk the dog X.Y.Z`.
   - `body` is the CHANGELOG section and is not empty.

**If the Release workflow fails**, do not delete or move the tag, because the tag is already
public. Fix the cause on `main`. If the tagged commit itself is fine, re-run the workflow for
the existing tag:

```
gh workflow run release.yml -f tag=vX.Y.Z
```

If the tagged commit is wrong, stop and ask the user. The usual answer is a new patch version,
not a rewritten tag.

## 10. Close the loop

1. Add a line to the step 6 `STATE.md` entry: *Released as `vX.Y.Z` (link), with CI, Validate
   and Release green, published, not a draft and not a pre-release.*
2. Commit it as `docs: record the published X.Y.Z release in STATE.md` and push.
3. Confirm with `git status -sb` that `main` matches `origin/main`.

Then give the user a short summary in plain, non-technical language:

- The version number and the release link.
- What changed for someone using the integration, in two or three sentences.
- That HACS will offer the update within the hour.
- Anything that was skipped, failed or needed their decision along the way.
