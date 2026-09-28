# Bland — hosted voice agents, and whether Prax should place phone calls

**Verdict: document + adopt a *capability*, not a vendor.** Add a governed
`place_call` tool — Prax phones a business for the user and returns a
transcript — behind a small provider interface, with Bland as a candidate first
provider because it is the shortest path to a working call. Keep Twilio for SMS
and for inbound calls to Prax. Do **not** move Prax's own voice conversations
onto Bland: that would hand the conversation to Bland's models and pathways,
and Prax would stop being the brain. Queued, not started — see the adopt
tracker.

Source: [bland.ai](https://www.bland.ai/), [pricing](https://www.bland.ai/pricing),
third-party pricing write-ups (below), and the September 2026 reporting on Meta
Muse's calling feature. Nothing here was exercised live.

## What Bland is

A hosted platform for AI phone agents. From its own site:

- **Everything in one per-minute rate**: "One per-minute rate covers the language
  model, speech-to-text, text-to-speech, and telephony." Third-party write-ups
  of the 2026 plans: **$0.14/min** with no platform fee (Start, capped at 100
  calls a day), $0.12/min at $299/month, $0.11/min at $499/month, plus
  **$0.015 per call attempt** and **$0.02 per SMS** in either direction.
- **Its own models**, "custom-made for phone calls", and a claimed **400 ms**
  response latency against a "1,240 ms" industry average (their figure,
  unverified).
- **Conversation design as "pathways"**, a REST API and webhooks, and CRM
  integrations (Salesforce, HubSpot, Zapier).
- **Telephony**: its own numbers, or bring your own Twilio or SIP.
- **Channels**: voice, SMS, iMessage and web chat, with shared memory.
- **Compliance claims**: SOC 2 Type II, HIPAA, PCI DSS v4.0; on-premises
  deployment for sensitive workloads. Proprietary; not open source.

## The Muse connection, stated precisely

The question was "Muse uses it now". What the sources support:

- **Muse's own calling** (launched September 2026) phones US businesses to book
  appointments, check stock and get quotes. Reporting by
  [404 Media](https://www.404media.co/meta-tests-muse-ai-agent-calls-that-are-actually-made-by-humans-in-a-call-center/)
  and others found Meta tested routing those calls to **human contractors**
  without telling users; internal tests showed humans lifting success to
  95–98%. Meta rolled the test back after employee objections.
- **Bland announced the Muse integration** (on X; the tweet itself was not
  reachable from our tooling) and documents a plan that connects Bland to Muse
  to give the agent its own phone number, for bookings and reservations.

So there is a real competitive feature here — *an agent that phones businesses
for you* — and Bland is now positioned as the phone line for personal agents
like Muse. What the sources don't settle is whether Meta's built-in calling
runs on Bland or Bland is an add-on a Muse user connects; the second is what
Bland's docs describe.

## Is it easier than Twilio?

For **outbound agent calls, yes — much easier.** Doing it ourselves on Twilio
means a real-time loop: streaming speech-to-text, the model, streaming
text-to-speech, interruption ("barge-in") handling, silence and voicemail
detection, and all of it under roughly a second or the other party talks over
the agent. Prax's current Twilio voice path doesn't do this; it is turn-based
TwiML `<Gather>`, fine for a person talking to Prax, poor for a live call with a
receptionist. Bland (or Retell, Vapi, Twilio ConversationRelay) sells exactly
that loop.

For **SMS, no.** Prax's SMS works on Twilio today; Bland's $0.02 a message buys
nothing Prax needs.

## What an adoption looks like

`place_call(to, goal, facts, allow_commit)` in a spoke:

1. **Consent first.** A call to a third party is an outward action in the
   user's name. It is HIGH risk and goes through out-of-band approval
   (`OUT_OF_BAND_APPROVALS_ENABLED`): the person sees who will be called and
   the brief, and approves it in TeamWork.
2. **A minimal brief.** Prax hands the provider only the goal and the facts the
   call needs ("book a haircut Thursday after 5, name TJ, up to $40"), never the
   conversation or memory. The provider sees one task.
3. **Disclosure.** The agent says it is an AI calling on someone's behalf. That
   is honest, which Prax requires, and it keeps us clear of the problem Meta
   ran into. Recording follows the provider's consent handling and the rules of
   the state being called.
4. **The result comes back as data.** Transcript, outcome and any commitment
   made (booked, quoted, declined) become an untrusted tool result, so it is
   tainted like web content. Nothing is committed beyond what the approval
   allowed.
5. **Keyless.** The provider API key belongs in the secrets proxy, not Prax.
6. **Call length is the user's to set, and a cut-off is never silent.**
   - A maximum call length with a sensible default (say 10 minutes), which the
     user can raise, lower or turn off (`CALL_MAX_MINUTES`, 0 = no limit), and
     override for one call ("this one may take a while, allow 30").
   - A limit hit mid-call is not a hang-up out of nowhere. Near the limit the
     agent is told to wrap up politely ("I need to go — can I call back?"),
     so a call that is making progress ends cleanly instead of being dropped.
   - When a call ends at the limit, Prax is told why ("ended at the 10-minute
     limit while the receptionist was checking availability") and tells the
     user, with the transcript so far, what was and wasn't settled, and the
     options: call back, or raise the limit and retry. A legitimate call cut
     short is a result the user must see, not a failure hidden as "done".
7. **A spending cap Prax enforces itself.** A daily call budget, user-set,
   checked before each call; prepaid balance with auto-refill off on the
   provider side. Neither depends on the provider's settings staying right.

The provider interface keeps Bland swappable: Retell and Vapi are like-for-like
hosted alternatives, and the self-hostable route (Pipecat or LiveKit Agents over
Twilio, with our own model through the proxy) keeps the conversation in-house
at the cost of building the real-time loop.

## Honest nuance

- **The hard part isn't the phone call.** Muse's own numbers say AI-only calls
  under-perform people. Expect real failure rates on phone trees, hold music,
  voicemail and accents, and report them honestly: "I couldn't get through"
  rather than an invented booking.
- **Bland's model runs the call, not Prax's.** During the call, Bland's model
  and prompt decide what is said. That is the price of the easy path, and the
  reason for a tight brief and an explicit allow-commit.
- **Cost is small for personal use**: a 3-minute booking is about $0.45.
- **Legal exposure is real.** AI-voice outbound calls fall under the FCC's
  robocall rules (TCPA), and state recording-consent laws vary. Calls placed at
  one person's direct request to one business are the low-risk end of that;
  anything automated or bulk is not.

## Billing: can it run away?

Bland bills from a credit balance. Its API exposes a "refill" amount — the
balance it tops back up to, or `null` if refilling is disabled. With refill
off it behaves as prepaid: put in $50, spend $50. With refill on, the card on
file is charged whenever the balance runs low, and no monthly cap on those
refills is documented. What happens to a call when the balance hits zero is
not documented either (its daily/hourly call limits do reject calls "rather
than incurring unexpected charges"). Ask Bland before relying on it. The
$299/$499 plans are monthly subscriptions; the Start plan needs no card.

## Recording

Recordings are retrievable per call as MP3 or WAV, with transcripts in the
call details. Not documented: whether recording must be enabled per call, how
long recordings are kept, and who handles consent. Several US states require
all parties to consent, so Prax states that the call is recorded at the start
and saves the recording and transcript to the user's workspace.

## Adopt tracker

| Item | Status |
|---|---|
| Governed `place_call` tool behind a provider interface, Bland first; HIGH risk + out-of-band approval, AI disclosure, minimal brief, tainted transcript, key in the secrets proxy | **queued** |
| Real-time voice for Prax's own inbound calls (replace `<Gather>`) | **parked** — separate decision; if done, prefer a loop where Prax stays the brain (ConversationRelay / Pipecat) |
| Move SMS to Bland | **declined** — no gain over Twilio |

## Sources

- [Bland](https://www.bland.ai/), [Bland pricing](https://www.bland.ai/pricing)
- [Bland AI pricing 2026 (getmacha)](https://www.getmacha.com/blog/bland-ai-pricing-explained),
  [CloudTalk guide](https://www.cloudtalk.io/blog/bland-ai-pricing/)
- [404 Media: Meta tests Muse calls made by humans](https://www.404media.co/meta-tests-muse-ai-agent-calls-that-are-actually-made-by-humans-in-a-call-center/),
  [Digital Trends](https://www.digitaltrends.com/computing/metas-muse-says-its-ai-but-a-human-might-be-making-your-call/),
  [technology.org](https://www.technology.org/2026/09/23/meta-muse-human-concierge-ai-phone-calls/)
