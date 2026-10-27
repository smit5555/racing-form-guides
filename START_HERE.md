# August results collection — review package

Prepared for smit5555/racing-form-guides. Nothing has been committed, pushed or started on GitHub.

This adds a separate, manually started collector. It reads Racing.com's public calendar and rendered final-result pages, then saves the displayed rows and source HTML. It does not call an AI model, train a model, change predictions, calculate returns or write to the repository.

## What to review

- `.github/workflows/collect-august-results.yml`: manual run, standard Ubuntu runner, read-only repository access, 7-day evidence retention.
- `tools/collect_august_results.py`: sequential browser collection, minimum 20 seconds between navigations/calendar requests, stops on access blocks or unexpected page structure. No proxy, stealth, hidden API or security bypass.
- `inputs/august_2026_meetings.json`: 171 meetings / 1,328 expected races from Luke's original August guides, with guide hashes.
- `collection_tests/test_collector.py`: offline checks for manifest coverage, source/date identity, title identity and access-challenge detection.

The calendar controls and one August result page were checked in the browser. Offline tests pass. A GitHub-hosted browser run has NOT yet been tested. Live collection can still encounter missing meetings, venue naming differences, abandoned races, access restrictions or page changes. Those stop the run and remain unresolved; nothing is guessed.

Update after pilot run 40595873244: fixed the month-change loading race. The first pilot stopped at `Calendar has no dated cells` while September was still loading. The collector now waits up to 60 seconds for a changed, nonempty grid; an empty grid no longer counts as a finished month change. It also dismisses the nonessential-cookie banner when present. The actual browser readiness predicate has a regression check for old grid -> blank grid -> new grid. This corrected version still needs one live pilot; do not start the full batches yet.

Version 2026-10-28.3, after pilot 40596401543: the calendar reached August but was still loading after filter changes. The collector now waits for the exact requested day after those changes and pins the browser timezone to Australia/Brisbane (GitHub runners otherwise inherit UTC, which changes midnight date keys). Cookie dismissal is checked again after loading. The ZIP includes failure date-cell diagnostics and the collector version in status.json. Seven offline checks pass, including actual JavaScript predicates for both loading failures and the Brisbane/UTC date boundary. This version has not completed a GitHub pilot yet.

## Installation after Luke approves publishing these files

Extract the setup ZIP. Add its contents to the ROOT of `smit5555/racing-form-guides`, preserving the `.github/workflows` folder. Do not put everything inside an extra `august_results_github_setup` folder. Review the changed files before committing. This package does not replace existing files such as `results.py`, `run.py`, model locks or feature builders.

## First run: small pilot

1. Open the repository on GitHub and choose **Actions**.
2. Choose **Collect August results**, then **Run workflow**.
3. Select batch **01-10**, mode **pilot**, then start it.
4. Open the completed run. At the bottom, under **Artifacts**, download the `august-2026-results-pilot-...` ZIP and attach it in this chat. Download within 7 days.

The first pilot collects Gilgandra, 1 August: six races. GitHub runs it independently; Codex does not need to stay open. No Codex or OpenAI API key is required. A run may take several minutes, plus GitHub's queue and browser installation time.

## After the pilot is verified

Run mode **batch** separately for **01-10** (55 meetings), **11-20** (53 meetings), and **21-31** (63 meetings). Download each ZIP and attach the three ZIPs here. Runs are queued one at a time. Allow hours per full batch. No need to copy logs during normal progress.

If a run stops or turns red, download its artifact anyway and attach that ZIP. It includes status, partial captures and a screenshot when available. Do not keep rerunning blocked batches: there is no automatic cross-run resume in this first version, so a rerun starts again. We will review the failure and retain usable captures before deciding the next step.

## Understanding the output

- `status.json` reports collection status and errors. `captured_pending_verification` means captured, NOT approved for backtesting.
- `captures/*.racing_com.json` retains exact source URLs, rendered race title, final status, rows, HTML and capture time. Missing SP stays as displayed; tote odds are never substituted.
- `sha256.json` records evidence hashes.

On import, the backtest project's existing checks must match every horse, runner number, meeting, race and date against the frozen guides and verify official SP coverage before settlement. Non-runs and missing results/prices need explicit review. Racing.com is an alternative published results source; captures are not falsely labelled Racing Australia originals.

## Cost and usage

There are no AI calls in this workflow, so waiting for collection does not consume Codex usage. GitHub documents standard hosted runners as free for public repositories; artifact storage has separate account limits. This workflow retains evidence for only seven days. Do not choose a larger paid runner.

References:
- https://docs.github.com/en/actions/how-tos/write-workflows/choose-where-workflows-run/choose-the-runner-for-a-job
- https://docs.github.com/en/actions/managing-workflow-runs/manually-running-a-workflow
- https://docs.github.com/en/actions/managing-workflow-runs/downloading-workflow-artifacts
- https://playwright.dev/python/docs/ci
