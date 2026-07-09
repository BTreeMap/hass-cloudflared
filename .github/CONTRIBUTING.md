# Contributing

When contributing to this repository, please first discuss the change you wish
to make via issue, email, or any other method with the owners of this repository
before making a change.

Please note we have a [code of conduct][coc], please follow it in all your interactions
with the project.

## Issues and feature requests

You've found a bug in the source code, a mistake in the documentation or maybe
you'd like a new feature? You can help us by submitting an issue to our
[GitHub Repository][github]. Before you create an issue, make sure you search
the archive, maybe your question was already answered.

Even better: You could submit a pull request with a fix / new feature!

## Pull request process

1. Search our repository for open or closed [pull requests][prs] that relates
   to your submission. You don't want to duplicate effort.

1. You may merge the pull request in once you have the sign-off of one other
   developer, or if you do not have permission to do that, you may request
   the reviewer to merge it for you.

## Maintainer release process

Releasing is intentionally a one-file operation:

1. Change the top-level `version` in `cloudflared/config.yaml` to a greater,
   stable semantic version such as `8.1.0`.
1. Open and merge the pull request normally.
1. After CI succeeds on the current `main` revision, the Release workflow
   publishes the Release Drafter draft under the matching `v8.1.0` tag and
   marks it as the latest release. The same workflow then deploys the stable
   versioned container images directly.

The workflow does nothing when the config version already matches the newest
version tag. It rejects malformed or decreasing versions, existing tags or
releases, stale CI revisions, failed CI, non-push runs, and ambiguous release
drafts. A failed release can be retried from the Actions page after correcting
the reported condition; no manual tag should be created. If a release exists
but its versioned or `stable` container manifest is missing, the next successful
CI run repairs that deployment automatically.

[coc]: /.github/CODE_OF_CONDUCT.md
[github]: https://github.com/BTreeMap/hass-cloudflared/issues
[prs]: https://github.com/BTreeMap/hass-cloudflared/pulls
