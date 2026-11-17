# Verification and honest reporting

A task is done when you have seen the result work where the user will use it. Report what you verified separately from what you only expect.

## Check the result yourself

- Before reporting done, observe the result in its real form: open the page, run the command, render the document, read back the file you wrote.
- For a user-facing change, exercise every visible control in the changed surface, including empty, error and narrow-width states. Take screenshots.
- Confirm the running app serves the new build. Changed source and passing tests do not prove the user's open app has the change.
- When several clients share a feature, check each client. A fix in one does not close the others.
- Right-size the check. A routine fetch or conversion with a tool that fails loudly needs about three tool calls, without an audit.

## Exit 0 proves only that the process ran

`ok: true`, `healthy`, `error: null` and exit code 0 show that a process finished. They do not show that the right data arrived. Check two things independently:

- **Which identity answered.** A config that falls back to a default credential returns an empty set without an error. Compare the authenticated account with the one you meant to use.
- **How old the data is.** A reader whose source app stopped can still report "up to date". Compare the newest timestamp with now.

Also confirm that the scheduler is actually running, because a server can serve stale output indefinitely. Treat an empty result from a process that may lack permission as possibly "no access": rerun from a context that has the grant. When you build a data adapter, make "not configured" and "configured and empty" produce different output, and fail loudly on an identity mismatch.

## Claims match evidence

- Every sentence of a conclusion traces to an output, a file or a source you read. Text claims only what the code ran.
- Label each claim: verified (and how), unverified (and what is missing), or reported by a source (which one). A number whose only source is the party it describes is a claim.
- An estimate carries its inputs. A forecast says "could" or "would".
- Unknown is not zero. Show unknown values as unknown.
- Workers report wrong results with the same confidence as right ones. Read their output critically and re-check the claim that matters most.
- Do not guess APIs, versions, flags, commit hashes or package names. Read the code or the docs first.

## Research and data work

- Check units, sample filters, time windows and group membership before reading results. Change one thing per test.
- Record data definitions in the project's data docs ([workspace-structure.md](workspace-structure.md)).
- Send results to the research-auditor before reporting them. The researcher and research-auditor roles hold the rules for equations and measures.

## Reporting

- Report every ledger item with one status: **done** (with evidence), **in progress** (with who runs it), **unverified** (with what is missing), or **declined** (with the reason). Other prompts and roles use these four.
- Lead with the answer, one line per item. Keep answers to questions apart from the work checklist.
- State what you could not check and why.
