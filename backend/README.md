# Sourcing Desk — backend

Multi-agent industrial parts sourcing. Give it a part number, it researches
the web and returns a landed-cost comparison table.

Full design: `../docs/sourcing-desk-design.html`

## Status

**P0 + P1 complete.** Foundation plus the Scout: it searches, ranks by
registry weight, reads pages, and extracts structured offers into the DB.

| Phase | What | State |
|---|---|---|
| P0 | Foundation: config, DB, registry, ingestion | done |
| P1 | Scout: search, rank, fetch, extract | done |
| P2 | Analyst: landed cost, comparison, sensitivity | done |
| P2.5 | Browser rung + manual paste rung | done |
| P3 | Purchase log, History agent, clarify node | done |
| P4 | Supervisor, critic, LangGraph wiring | done |
| P5a | Evaluation harness (F11) | done |
| P5b | Equivalence checking | done |
| P5c | RFQ email, frontend (F13), deploy (F14) | done |

**All fourteen course features are implemented.** One model throughout:
`google/gemini-2.5-flash-lite`. The evaluation confirmed a stronger model
was not needed — see below.

## Run it

```bash
cd backend
.venv/Scripts/python -m uvicorn app.api:app --reload --port 8000
```

Open http://localhost:8000 — new request, comparison table, quantity
sensitivity, streaming agent trace, RFQ approval queue and purchase logging
on one page.

### Starting a request

Type it the way you would say it:

> I need 50 FST100-2006A, budget $3000, by March. RS485 is a hard requirement.

The clarify node parses that into part, quantity, budget, deadline and
mandatory specs, then either creates the request or comes back with at most
two questions. In the example above it resolves `by March` to `2027-03-01`
(March 2026 has passed) and states that as an assumption rather than
silently applying it.

**Nothing partial gets created.** If quantity is missing the request is not
written — a half-specified request that quietly gets searched anyway produces
a confident, useless table.

Specs given as hard requirements are stored with `is_mandatory = 1`, which is
what disqualifies an alternative part later. That flag is only ever set from
what he actually said, never inferred.

### Budget

Once offers are priced, the table headline reports against the budget:

```
Cheapest landed: Shenzhen Tiancheng Trading at $60.15/unit.
Budget $3200.00 — cheapest total $3007.50 at qty 50. Within budget, $192.50 to spare.
```

Three distinct states, because collapsing them misleads: `no_budget` (none
given), `not_yet_priced` (a budget was given but nothing is priced yet, so it
has not been tested), and `checked`.

Deployment: `../docs/deploy.md`. Docker image, GCP e2-small, costs.

### The extraction ladder, as actually observed

| Rung | Works on | Notes |
|---|---|---|
| `fetch` | manufacturer sites, Made-in-China | fast, reliable, most of our data |
| `browser` | robu.in and other JS/403 sites | Playwright; recovered a 403'd page cleanly |
| `manual` | **Alibaba, 1688** | CAPTCHA-walled — see below |

**Alibaba serves a slider CAPTCHA.** Confirmed: HTTP 200, title
"CAPTCHA Verification", 166 characters of body. We do not attempt to solve it
— that is both off-limits and an arms race that breaks weekly. Alibaba goes
to `scripts/paste.py` or to an RFQ, which is the better path anyway since its
listed prices are indicative rather than quantity prices.

## Setup

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt   # Windows
cp .env.example .env                            # then paste your OpenRouter key
```

## Verify

```bash
.venv/Scripts/python scripts/init_db.py           # creates DB, seeds registry
.venv/Scripts/python scripts/check_vectorstore.py # embeddings + Qdrant, no key needed
.venv/Scripts/python scripts/check_llm.py         # OpenRouter, needs the key
```

`init_db.py` and `check_vectorstore.py` are re-runnable and non-destructive.
`init_db.py --reset` deletes the database first.

## Run the Scout

```bash
# read specific pages -- needs only OPENROUTER_API_KEY
.venv/Scripts/python scripts/scout.py FST100-2006 --qty 50 \
  --url "https://firstsensor.en.made-in-china.com/product/..."

# search Google + Baidu -- also needs SERPAPI_KEY
.venv/Scripts/python scripts/scout.py FST100-2006A --qty 50
```

Observed on the three real sources currently seeded:

| Source | Rung | Result |
|---|---|---|
| Made-in-China | fetch | $40 USD, MOQ 1, 7-day lead, confidence 0.90 |
| firstratesensor.com | fetch | real supplier, no listed price → `needs_rfq` |
| robu.in | browser | 403 on plain fetch → falls through to manual paste |

Those three are the whole design in miniature: a priced listing, a supplier
that has to be asked, and a site that needs a rung we have not built.

Live search confirmed the Baidu hypothesis: it returned `firstsensor.cn`
(菲尔斯特传感器有限公司), the Chinese-language manufacturer site, which Google
did not surface at all.

## Compare offers

```bash
.venv/Scripts/python scripts/compare.py 1 --qty 50
.venv/Scripts/python scripts/compare.py 1 --sensitivity 1,10,50,200
```

```
supplier                        listed incoterm   freight  duty+vat   LANDED/u  uplift
Shenzhen Tiancheng Tradi       260 CNY EXW         250.00    764.88      60.15     56%
Hunan Firstrate Sensor C        40 USD EXW?        250.00    787.76      61.96     55%
Tashkent Instrument Supp        71 USD DDP           0.00      0.00      71.71      1%
```

Quantity sensitivity — the winner genuinely changes with volume:

| qty | winner | landed/unit |
|---|---|---|
| 1 | Tashkent Instrument Supply | $71.71 |
| 10 | Hunan Firstrate | $67.60 |
| 50 | Shenzhen Tiancheng | $60.15 |
| 200 | Shenzhen Tiancheng | $52.87 |

At one unit, buying locally is cheapest because freight minimums dominate.
At volume the Chinese EXW quote wins. That is the analysis the product exists
to produce, and it is why quantity is a first-class variable rather than a
footnote.

## Paste a blocked page

```bash
python scripts/paste.py FST100-2006A --qty 50 --url https://alibaba.com/... --file page.txt
```

Extraction is identical to the scraped rungs. On a pasted Alibaba listing with
tier pricing it correctly picked **$33.00** — the 50–199 piece tier — rather
than the headline $38 or the floor $32.

## Purchase log and questions

```bash
python scripts/log_purchase.py "bought 56 FST100-2006A from Hunan Firstrate, \$41 each, came in 18 days"
python scripts/ask.py "what have we paid for FST100 sensors?" --sql
```

The log extracts what it can, batches every gap into one message, then saves:

```
Filling the gaps:
  price_basis    was that price FOB, EXW, DDP or delivered/landed?
  freight_paid   what did freight cost, if you know?
  ...
> FOB, freight was 90, duty and vat about 150, ordered start of june, all fine

saved purchase #2
  landed per unit   $45.29
  implied freight   $4.02/kg -- beats the default estimate
```

That last line is the point: a real invoice replaces the guessed freight rate.

## The multi-agent graph

```bash
python scripts/chat.py "what did we pay for FST100 last time?"
python scripts/chat.py "cheapest landed?" --request 1 --qty 50 --evidence
python scripts/chat.py --graph        # mermaid, for the README and defence
```

```
question -> supervisor -> [part|scout|history|analyst] -> supervisor
                 |
              finish -> generate -> critic -> END
                             ^         |
                             +-- revise+
```

Star topology: every specialist returns to the supervisor, none talk to each
other. A mis-route therefore shows up in exactly one place.

**Termination is guaranteed three ways** — a hop budget in the supervisor
(checked *before* the model is consulted, so an exhausted run cannot be talked
into another hop), a revision cap in the critic, and LangGraph's own
recursion limit. Repeat suppression stops the supervisor calling the same
agent twice, which is the standard way these graphs spin forever.

Control flow is plain Python throughout. `route_after_supervisor` reads a
string the model wrote into state and returns a node name; the model never
jumps anywhere itself.

### The critic earns its place

Two layers, cheapest first:

1. **Uncited numbers, in code.** Every figure in the answer must appear in the
   evidence. No LLM call needed.
2. **Grounding and scope, by LLM.** Are the claims supported, and does the
   answer address the question?

Observed rejections, both real errors:

- An answer attributed purchase-history data to "the datasheet". Rejected for
  misattribution, fixed on retry.
- An answer named the wrong cheapest listed price. Rejected twice — correctly,
  see below.

## Evaluation

```bash
python scripts/evaluate.py --cost      # deterministic, free, must always pass
python scripts/evaluate.py --routing   # supervisor accuracy
python scripts/evaluate.py --answers   # end to end
python scripts/evaluate.py --json results.json
```

20 cases, average answer latency ~4 s on `gemini-2.5-flash-lite`.

**Scores vary between runs.** Typical is 20/20, but a routing case occasionally
flakes to 6/7 and passes on the next three runs unchanged. Temperature is 0,
so this is provider-side non-determinism, not prompt drift.

Report a pass rate over several runs, never a single result. A one-shot 100%
on a stochastic system is a number that will embarrass you the first time
someone re-runs it.

| Group | Cases | What it measures |
|---|---|---|
| costing | 7 | Landed-cost arithmetic. No model involved. |
| routing | 7 | Does the supervisor pick the right specialist? |
| answers | 6 | Does the whole graph produce a correct, grounded answer? |

This measures **correctness, not plausibility**, because the ground truth is
genuinely known: the landed-cost maths is hand-checkable, the datasheet
answers are in the datasheet, the purchase history is what we logged.
Two of the costing expectations (`$71.71` and `$64.96`) were computed by hand
before the code was run.

The answer cases include traps, not just targets. One requires `IP68`, which
exists **only in an image caption** — passing it proves the multimodal path
works. Another forbids the phrase "cheapest listed price is $40", which is
the answer you get if you fail to convert CNY.

### What evaluation actually caught

Routing started at **6/7**. The failure: *"what is the landed cost per unit?"*
routed to `part` (datasheets) instead of `scout`/`analyst`.

The obvious response is to point `MODEL_REASONING` at a stronger model. That
was tried — `gemini-2.5-flash` scored **exactly the same 6/7, same failure**.
So it was never a capability problem.

The real cause was a missing dependency in the supervisor prompt. With empty
state the model reasoned "understand the part first", which is sensible in
general and wrong here: landed cost needs *offers*, not specifications.
Adding the dependency rules — `analyst` requires `scout` output first, and a
question containing price/cost/cheapest is never a `part` question — took
routing to **7/7 on the cheap model**.

Worth keeping: measure before upgrading. The upgrade would have cost money
and fixed nothing.

## Equivalence checking

```bash
python scripts/equivalent.py FST100-2006 RS-ECTH-N01 --demo
```

```
NOT A SUBSTITUTE
blocked on:
  output_signal: need RS485 Modbus RTU, has 4-20 mA analog
```

The split that matters: the **model** judges compatibility per parameter,
because that needs domain semantics. The **code** decides disqualification,
because "fails a mandatory spec" must never be a judgement call — a model
that returns `hard_fail` on a mandatory parameter cannot then talk itself
out of it.

Three buckets, and the third is the one usually got wrong: `hard_fail`
(no price makes it acceptable), `soft_diff` (a trade-off to weigh), and
`unknown` (**not stated — ask, never assume**). Unstated mandatory parameters
become explicit questions for the supplier rather than silent passes.

Building this surfaced a data-quality bug: the extractor had been storing
marketplace commercial metadata as if it were technical specification, so
equivalence solemnly compared `trademark`, `transport_package` and
`production_capacity`. `scout.is_commercial_field` now filters those at
ingestion, and HS code is promoted to `parts.hs_code` where it belongs, since
it drives the duty rate rather than describing the part.

**Known limitation:** spec keys are not normalised, so `output_signal` and
`signal_output` appear as separate parameters. Harmless here — both were
judged correctly — but it inflates the comparison.

## RFQ email — the fallback

```bash
curl -X POST localhost:8000/rfq/draft/5        # draft for unpriced suppliers
curl -X POST localhost:8000/rfq/1/approve      # he reviews, then approves
curl -X POST "localhost:8000/rfq/1/send?dry_run=false"
```

The drafted email asks for exactly what the costing model needs, including
**shipping weight** — which is where the freight estimate stops being a guess.

> **Real sending is written but unverified.** `EMAIL_ADDRESS` and
> `EMAIL_PASSWORD` are empty, so SMTP has never actually run. Drafting, the
> approval gate and dry-run are all tested. To send for real you need a Gmail
> **App Password** (16 characters, requires 2FA) — a normal account password
> has been rejected by Google since 2022.

**The gate is code, not convention.** `mailer.send` refuses anything whose
status is not `approved`, there is no flag that overrides it, and it is the
only function that sends:

```
POST /rfq/1/send        -> 403 "rfq #1 is 'drafted', not 'approved'."
POST /rfq/1/approve     -> {"status": "approved"}
POST /rfq/1/send        -> {"sent": false, "note": "dry run"}   # opt-in
```

`dry_run` defaults to **true**. Sending is opt-in on every single call.
An agent emailing real suppliers under someone's name is the one irreversible
action in this system, so it is gated three ways: status check, dry-run
default, and human review of the text itself.

Replies are matched by a reference code in the subject line, never guessed
from the sender address, then parsed into the same offer schema as scraped
listings — marked `verified`, because a quote addressed to us at our quantity
is better evidence than a public listing.

## Frontend

One HTML file, no build step, served by FastAPI. `/chat` streams the agent
trace as server-sent events straight off the LangGraph stream, so you watch
the supervisor delegate, the critic reject, and the answer arrive:

```
supervisor  analyst (the scout has found offers, so the analyst can compare)
analyst     costed 3 offers
supervisor  finish
generate
critic      REVISE -- claims Shenzhen's listed price is...
generate
critic      approved
```

That is a far better demonstration of a multi-agent system than tokens
appearing in a chat bubble — you can see which agent is working.

### On the critic being load-bearing

Watch the trace above. On `flash-lite`, answers to comparison questions
frequently need one or two revisions before they are correct — the model
conflates a landed figure with a listed one, or misattributes a source. The
critic catches these. It is not a rubric checkbox on this model; remove it
and wrong answers ship.

That is also why the revision cap is a real risk rather than a formality: if
the third attempt is also wrong, the cap passes it through. It happened
during development, and the fix was better evidence, not more retries.

## Three guards where prompts were not enough

Each of these is a real bug that reached a passing test before being caught.

**`price_basis` is never inferred.** Told "never invent a price basis", the
model read `$41 each` as `landed` — the most favourable reading, silently
wrong, and it would have poisoned every later comparison ($41 FOB lands near
$60). `purchase._scrub_inferred_basis` blanks the field unless the buyer's own
words contain a basis term.

**Quantity is always asked.** The clarify node marked `find me FST100-2006A`
as ready with no quantity, despite quantity being the one field that always
changes the answer. `clarify.check` now injects the question in code.

**The History agent never does arithmetic in prose.** Given a row with
`landed_per_unit = 45.29` and `quantity = 56`, it reported a total of
`$2,536.24` — computed in text, and disagreeing with the stored `$2536.00`.
It is now instructed to quote only literal values and say when a figure is
absent.

**Cross-currency comparison is never left to the model.** Asked for the
cheapest listed price, it was handed `260 CNY` and `$40 USD` and picked $40.
CNY 260 is $38.67. The critic rejected it twice and was right both times, then
the revision cap forced the wrong answer through — a good demonstration that a
retry cap papers over a bad prompt rather than fixing it. `analyst_node` now
states the USD equivalent for every row and pre-computes both winners, so the
model reads a comparison instead of performing one.

The pattern: a prompt is guidance, a code check is a guarantee. Anywhere being
wrong is both silent and expensive, write the check. Note that in each case
the first version passed a test before the bug was found — these were caught
by reading output carefully, not by anything failing loudly.

## Layout

```
app/
  config.py          settings from .env — nothing else reads os.environ
  llm.py             OpenRouter client; default/reasoning/vision roles
  vectorstore.py     FastEmbed (local) + embedded Qdrant
  db/
    schema.sql       written to be readable by a text-to-SQL agent
    database.py      connection, init, transactional session
    seed.py          source registry seed — weights, not filters
  ingest/
    datasheet.py     PDF -> text chunks + captioned images -> Qdrant
  sources/           (P1) search and extraction
scripts/             runnable checks, each prints what it did
data/                gitignored: sourcing.db, qdrant/, datasheets/
```

## Decisions worth knowing before editing

**Embeddings run locally.** FastEmbed with `bge-small-en-v1.5`, 384 dims.
No key, no cost, no rate limit in the hottest loop. OpenRouter does offer an
`/embeddings` endpoint if this ever needs to move off the machine.

**Three model roles, one gateway.** `MODEL_DEFAULT` handles extraction and
parsing, `MODEL_REASONING` handles supervisor routing and equivalence
checking, `MODEL_VISION` handles datasheet figures. All three start as
`gemini-2.5-flash-lite`. Split them only when the golden set says routing or
equivalence is weak — it's an env var, not a refactor.

**Images in datasheets are captioned, not skipped.** Spec tables and wiring
diagrams are frequently images inside the PDF. `check_vectorstore.py`
demonstrates this: the query "ingress protection rating" retrieves the
captioned figure, because IP68 appears nowhere in the extracted text.

**The schema is flat on purpose.** Spelled-out column names, no abbreviations,
no clever joins for common questions. Vague schemas are where text-to-SQL
produces valid-looking wrong answers.

**`part_specs` is key/value.** He sources anything technical, so a fixed spec
column set would need a migration per category. `is_mandatory` marks the specs
where a mismatch disqualifies a part outright.

**`purchases.price_basis` is NOT NULL.** $41 FOB and $41 delivered are
different numbers; storing one without the other silently corrupts every
future comparison.

**Search results must be cached.** SerpApi free tier is 250/month and one
thorough sourcing run can burn five. `search_cache` exists for this.
