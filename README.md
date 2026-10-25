# Async Support Dashboard

Discovery of remote support jobs with text-channel keywords and no explicit
heavy phone duties. Accepted roles may include occasional calls.

## Updater

Python 3.11+; no dependencies or API keys:

```sh
python update_jobs.py
python -m unittest -v
```

`jobs.json` is a JSON array with `title`, `company`, `date_posted` (UTC ISO 8601),
`source_link`, and `tags`. The default age limit is 45 days; override with
`--max-age-days 30`. Use `--output path/to/jobs.json` to change the output path.
Descriptions are used for screening but are not republished.

Sources:

- [We Work Remotely support RSS](https://weworkremotely.com/categories/remote-customer-support-jobs.rss)
- [Remotive support API](https://remotive.com/api/remote-jobs?category=customer-support)

Display the source name from `tags` beside each source link. Both sources require
attribution. Follow [WWR's feed guidance](https://weworkremotely.com/remote-job-rss-feed)
and [Remotive's API terms](https://github.com/remotive-com/remote-jobs-api): link to
the supplied job URL, name Remotive as the source, and do not gate its listings
behind signup/email collection or syndicate them to third-party job boards.
Remotive data is delayed by 24 hours; twice-daily fetching fits its published
guidance. Review source terms before adding monetization.

The filter requires a support/customer-experience title and at least one of
chat, email, ticket(s), Zendesk, Intercom, Slack, Discord, asynchronous, async,
or written (case insensitive). It rejects Call Center Agent titles and explicit
heavy-duty phrases: heavy phone volume, high-volume calls, cold calling, inbound
call queue, or telemarketing. One such phrase is enough; narrow negations such as
"no cold calling" are exempt. Occasional phone support and rare calls are allowed.
The `text-oriented` tag does not certify zero calls. Keywords cannot verify an
employer's actual duties; consult the listing, including its location restrictions.

Results are deduplicated by URL or normalized company/title and sorted newest
first. Missing required metadata is rejected. If both feeds succeed but no active
jobs match, the updater writes three fictional examples labeled `Sample Listing`
in both titles and tags. Their fixed dates prevent needless commits; their
`example.invalid` URLs cannot serve as application links. Frontends must display
the sample labels and disable apply buttons for these records. Live matches
replace all samples on the next successful run. If either feed fails, the updater
exits 1 and preserves the previous file without inserting samples;
atomic replacement prevents incomplete writes. Unchanged results do not rewrite
the file. A prolonged outage can leave old jobs visible; surface data freshness
when building the frontend.

## GitHub Actions integration

The workflow at `.github/workflows/scrape.yml` runs at 00:17 and 12:17 UTC daily
and supports **Actions > Update support jobs > Run workflow**. It tests first,
updates the feed, and commits/pushes only changed `jobs.json` to the default branch.
Concurrent runs are serialized. Failed tests or feeds stop the job before commit.

Push these files to the repository's default branch to enable scheduling. The
repository must allow Actions write permissions, and branch rules must permit
the bot's data commits. The workflow requests only `contents: write` and uses
the built-in `GITHUB_TOKEN`; no personal access token is required.

Connect the host's Git deployment integration, or optionally add a repository
Actions secret named `DEPLOY_HOOK_URL` with a Vercel/Netlify deploy hook for the
default branch. After a successful changed-data push, the workflow POSTs to the
hook if configured. Avoid enabling both deployment routes if they cause duplicate
builds. No hook is called on unchanged data or failed source fetches. A failed
hook fails the run; it is not automatically retried on later unchanged runs.

GitHub schedules can be delayed, and public-repository schedules are disabled
after 60 days without repository activity. See the
[schedule documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).

The updater has no paid services. Hosting and Actions remain subject to provider
quotas and terms; free service indefinitely cannot be guaranteed. The workflow is
prepared locally; remote scheduling and deployment require the setup above.
