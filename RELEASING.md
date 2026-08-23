# Releasing `augmentworks-target`

Releases are built in GitHub Actions and uploaded with PyPI Trusted Publishing.
Do not upload files from a local `dist/` directory and do not store a PyPI API
token in GitHub.

## One-time account configuration

1. Create separate accounts on [PyPI](https://pypi.org/) and
   [TestPyPI](https://test.pypi.org/), verify both email addresses, enable 2FA,
   and store the recovery codes.
2. In the GitHub repository settings, create environments named `testpypi` and
   `pypi`. Restrict both environments to tags matching `v*`. Require Jeff's
   manual approval for the `pypi` environment; if Jeff is the only reviewer,
   leave "Prevent self-review" disabled.
3. Create a pending Trusted Publisher at
   <https://pypi.org/manage/account/publishing/> with:

   ```text
   PyPI project name: augmentworks-target
   GitHub owner: jeffskafi
   Repository: augmentworks-target
   Workflow: publish.yml
   Environment: pypi
   ```

4. Create the equivalent pending publisher at
   <https://test.pypi.org/manage/account/publishing/>, changing only the
   environment to `testpypi`.
5. Add a GitHub tag ruleset for `v*` that restricts tag creation, updates, and
   deletion to repository administrators. Release tags are immutable inputs to
   the publishing workflow.
6. Enable private vulnerability reporting in the repository's security
   settings so the preferred reporting path in `SECURITY.md` is available.

A pending publisher does not reserve the package name. The first successful
upload creates the project.

## Release checklist

1. Update `__version__` in `src/augmentworks_target/__init__.py`. Hatchling
   reads the package metadata version from that single source.
2. Update version-pinned documentation links and installation examples.
3. Merge the release changes only after CI passes on every supported Python
   version and the distribution-validation job succeeds.
4. From the exact commit on `main`, create and push an annotated tag equal to
   `v` plus the package version, for example `v0.1.0`. Pushing that protected
   tag starts the `Publish Python distributions` workflow.
5. Let the workflow build one wheel and one source distribution, validate both,
   publish them to TestPyPI, compare the remote TestPyPI hashes with the
   workflow artifacts, and install the TestPyPI package in a clean environment.
6. Review the pending `pypi` environment deployment only after every preceding
   job succeeds. Approving it publishes the same downloaded artifacts to PyPI
   with Trusted Publishing and generates PyPI attestations.
7. After the PyPI job succeeds, create and publish the GitHub Release for the
   existing tag. This avoids advertising a release whose package upload failed.
8. Verify the production installation in clean Python 3.10 and 3.13
   environments:

   ```bash
   python -m venv .test-release
   source .test-release/bin/activate
   python -m pip install "augmentworks-target[fastapi]==0.1.0"
   python -c "import augmentworks_target; print(augmentworks_target.__version__)"
   augmentworks-target --help
   ```

## Failed releases

PyPI filenames are immutable. A file cannot be replaced even after deletion.
If a release is broken, yank it and publish a new version; never rebuild and
reuse the old version number.
