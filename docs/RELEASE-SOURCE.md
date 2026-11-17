# Regular desktop source release

This snapshot includes the regular Library Code desktop source and shared backend/browser client. It does not include an Official app bundle, provider extensions, user profiles or collected conversations.

The desktop build marker is `20261117T064454Z`, source hash `dfdb3dbba3daef43657e10f3f00e015ca9e3ee8b1b17b6a34308f0072fb86543`, matching the tested regular build. Two synthetic directory QA fixtures use a generic research name in this public copy. They are excluded from the production bundle; rebuilding records a new source hash because the source marker includes fixtures.

The source export preserves upstream attribution and literal SSH login-host deny rules. These aliases carry no username, credentials or remote address; deleting them would weaken the guard against work on login nodes. Synthetic fixture paths use generic users. No scan finding is silently ignored.
