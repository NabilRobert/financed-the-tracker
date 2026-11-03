# Independent review brief

The one review before the [real-data gate](slice-plan.md#the-real-data-gate) (gate line
11). Give this page, and nothing else from the build, to a fresh session that has never
seen this project, or to a person.

## The task

Try to break the tracker before anyone's real financial records go into it. You have the
repository at `main`, and you can run it: the README says how. Assume it holds the money
of up to five people, and that WhatsApp can ban the number at any time.

Start from [threat-model.md](threat-model.md). For each threat marked Done, try to prove it
isn't. Then look for threats it doesn't name.

## Where to look hardest

- **One person reaching another's data:** row-level security, the code's filters, the
  `SECURITY DEFINER` functions, and typed answers that carry no id.
- **A forged or replayed webhook,** and anything that reaches code before the signature
  check.
- **Content leaking:** message text, amounts, numbers or ids in logs, spans, errors,
  exports or the database.
- **Money:** a wrong amount written without a question. The parser's corpus is written by
  Claude, so real wording is the gap.
- **Deletion:** anything left behind, or brought back.
- **The deployment:** `ops/compose.yaml`, the `Dockerfile` and the backup script.

## Rules

- Invented data only. No real number, and no message to anyone.
- Don't push, open issues or post anything. Write findings to `private/review-findings.md`
  (git-ignored), each with a reproduction, its severity, and a suggested fix.
- A finding without a reproduction is reported as suspected.

Every finding is fixed, or accepted in writing by Arthur, before the gate is signed. They
reach the public repo only once fixed.
