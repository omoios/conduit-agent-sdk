# Test Plan

This seed repository does not implement the SDK yet. This file defines the first tests that should exist once implementation begins.

---

## First required tests

```txt
mock-run.test.ts
  - starts a mock run
  - streams ordered events
  - resolves completed result

event-schema.test.ts
  - every event matches envelope
  - sequence increments strictly
  - terminal event closes stream

result.test.ts
  - result includes status
  - result includes finalOutput
  - failed result includes failure object

redaction.test.ts
  - known secret values are removed
  - redactionStatus is set to redacted
```

---

## Later tests

```txt
fake-process-adapter.test.ts
acp-adapter.test.ts
conductor-adapter.test.ts
approval.test.ts
buggy-calculator.test.ts
docker-sandbox.test.ts
github-draft-pr.test.ts
environment-detect.test.ts
```
