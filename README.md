# AI Safety Daily Digest

**📰 Read it live: [ai-safety-digest.vercel.app](https://ai-safety-digest.vercel.app)** — a new edition every morning.

A daily AI-curated digest of the AI safety landscape — ~55 sources (labs, arXiv,
LessWrong/Alignment Forum, think tanks, Substacks, newspapers, podcasts) filtered
and summarized by Claude, rendered to a local website and emailed to you every
morning before 9am.

```
fetch → prefilter → triage (Haiku) → curate (Opus) → render → email
```

Cost: free under a Claude Pro/Max subscription (uses the Claude Code login on
this Mac); or ~$0.20–0.30/day if you set an `ANTHROPIC_API_KEY` instead.

## One-time setup

1. **Claude access — nothing to do.** The pipeline uses the Claude Code login
   already on this Mac (your Max subscription) via headless `claude -p`; usage
   counts against Max limits, which one run/day barely touches. Optionally, set
   `ANTHROPIC_API_KEY` in `.env` to use the pay-as-you-go API instead
   (~$0.25/day) — the code switches automatically if the key is present.
   ```bash
   cp .env.example .env    # then fill in the Gmail values below
   ```

2. **Gmail App Password** (for the daily email):
   - Enable 2-Step Verification at myaccount.google.com/security (if not already on)
   - Create an App Password at myaccount.google.com/apppasswords, name it "AI Safety Digest"
   - Paste the 16-character password into `.env` as `GMAIL_APP_PASSWORD`

3. **Swallow the back catalogue** so your first edition only has genuinely new items:
   ```bash
   ./.venv/bin/python -m digest bootstrap
   ```

4. **Test the pipeline** (each step is independent; the free ones first):
   ```bash
   ./.venv/bin/python -m digest sources validate      # all feeds parse? (free)
   ./.venv/bin/python -m digest prefilter --explain   # keyword filter preview (free)
   ./.venv/bin/python -m digest run --no-email        # full run, no send (~$0.25)
   open site/index.html                               # check the website
   ./.venv/bin/python -m digest email --input data/curated.json --test  # test email
   ```

5. **Install the schedule** (07:30 daily, 08:15 retry; fires at next wake if asleep):
   ```bash
   cp com.kkempkey.aisafetydigest.plist ~/Library/LaunchAgents/
   launchctl load ~/Library/LaunchAgents/com.kkempkey.aisafetydigest.plist
   ```

## Managing sources

Sources live in `config/sources.json` (tier 1 = trusted, unfiltered; tier 2 =
keyword-filtered). Edit by hand or use the CLI:

```bash
python -m digest sources list                  # all sources + health
python -m digest sources add --url URL         # validated add with preview
python -m digest sources test "Politico Tech"  # what would it fetch today?
python -m digest sources disable NAME          # mute without deleting
python -m digest sources discover HOMEPAGE     # find a site's feed
python -m digest sources validate              # re-check everything parses
```

After adding a source, run `python -m digest bootstrap` once so its back
catalogue doesn't flood the next edition.

Keywords for tier-2 filtering are in `config/keywords.json`; preview the effect
with `python -m digest prefilter --explain`. The editorial voice lives in
`EDITORIAL_RUBRIC` in `digest/curate.py`.

## Notes

- Sources returning zero items for 7+ days are flagged in the email footer.
- arXiv feeds are empty on weekends — that's arXiv, not a bug.
- Lawfare, Brookings, and RAND are disabled (blocked/broken feeds as of 2026-08);
  notes are in `sources.json`.
- The digest always sends: a quiet-day edition, or a failure notice if the
  pipeline broke. Silence means the Mac never ran it.
- Logs: `data/run-YYYY-MM-DD.log`. State: `data/digest.db` (SQLite).
