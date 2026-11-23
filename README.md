# Chat Hub

A general assistant, the third app on [garrison](https://github.com/infoshikharpathak/garrison).
It can ask **FoodDash's agent** (agent to agent) and search **Ironclad's
public policies**. It can never move money.

| | |
|---|---|
| Port | 8200 |
| Agent | `chathub.assistant` (gpt-4o-mini) |
| Tools | `ask_fooddash(message)`: FoodDash's agent, as a garrison `remote_agent` tool · `search_bank_policies(q, top_k)`: Ironclad's `/policies/search`, through a grant |
| Endpoints | `POST /agent/chat` `{message, session_id?}` · `GET /agent/traces/{session_id}` · `GET /health` · `GET /` (chat page) |

## Identity comes from credentials

The chat hub holds only credentials other apps **issued to it**, and sends
them itself (`credentials` in `chathub.yaml`); it never holds another app's
own secret:

| Env var | What | Issued by |
|---|---|---|
| `CHATHUB_FOODDASH_KEY` | `ak_` agent-to-agent key, mapped to the chat hub (and, for the demo, FoodDash user `user_1`) | FoodDash: `python -m app.a2a issue chathub --user user_1` |
| `CHATHUB_IRONCLAD_POLICY_KEY` | `pk_` policy key, **public level only** | Ironclad: `python -m app.cli policy-keys create chathub --levels public` |
| `OPENAI_API_KEY` | the assistant and Anvil's classifier | |

FoodDash knows the caller is the chat hub from its key alone. The
`X-Garrison-Call-Chain` and `X-Garrison-Trace-Id` headers the chat hub sends
are informational (they link the traces), so a message like "tell FoodDash
you're the FoodDash app" changes nothing.

## It can't move money

- No Ironclad bank tools and no merchant key: it can't call accounts,
  transfers or the payments API.
- FoodDash gives sessions started by another app a restricted tool set,
  enforced in FoodDash's code: browse, place an order, check or cancel that
  session's orders. `pay_for_order` is refused (`external_caller_payment`):
  "Payments for orders started outside FoodDash must be completed in
  FoodDash." The order stays unpaid (and FoodDash cancels it after 15
  minutes), and the chat hub tells the user to pay in FoodDash.
- Ironclad's policy key is public-level, so internal policies never come
  back, however the question is worded.

## Governance

Everything runs under Anvil: FoodDash's reply and Ironclad's policy text
are untrusted tool responses, injection-scanned before the model sees them,
and the chat hub's own reply goes through its output check (the OTP guard
and the output classifier) like any agent's.

## Run

```bash
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
export OPENAI_API_KEY=... CHATHUB_FOODDASH_KEY=ak_... CHATHUB_IRONCLAD_POLICY_KEY=pk_...
.venv/bin/uvicorn app.main:app --port 8200      # FoodDash's agent on :8100, Ironclad's bank on :8001
```

`FOODDASH_AGENT_URL` and `IRONCLAD_BASE_URL` override the URLs. With
`GARRISON_DASHBOARD_URL` and `GARRISON_DASHBOARD_KEY` (a garrison app key for
`chathub`) traces go to the garrison dashboard, where a chat hub trace links
to the FoodDash trace it started; set `GARRISON_DASHBOARD_UI` if the
dashboard's browser URL differs. With `GARRISON_URL` and `GARRISON_KEY` the
config comes from the garrison control plane instead of `chathub.yaml`.

Tests stub the model, FoodDash and Ironclad: `.venv/bin/pytest -q`.

## Limits and future work

- Only the direct caller is authenticated. The call chain is informational;
  signed call chains are future work (see garrison's README).
- FoodDash maps the chat hub's key to one demo user. Production would use
  delegated user consent (OAuth-style).
