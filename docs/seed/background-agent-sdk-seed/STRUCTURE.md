# STRUCTURE.md

This document describes the file structure for the hypothetical Background Agent SDK project and how the structure should evolve as implementation becomes more real.

The project should begin small. Do not start with a giant monorepo if the mock SDK is not passing.

---

## 1. Starting structure: v0 source package

```txt
background-agent-sdk-seed/
  README.md
  SOURCE.md
  AGENTS.md
  STRUCTURE.md
  docs/
    API.md
    EVENTS.md
    IMPLEMENTATION.md
    DECISIONS.md
    REFERENCES.md
  examples/
    buggy-calculator/
      package.json
      src/
        add.ts
        add.test.ts
  tests/
    README.md
```

Purpose:

- Capture direction.
- Define public API.
- Define standard events.
- Define tests and success criteria.
- Make the idea portable to other chats and projects.

No actual SDK package is required yet.

---

## 2. v0.1: smallest working SDK

```txt
background-agent-sdk/
  package.json
  tsconfig.json
  vitest.config.ts
  README.md
  SOURCE.md
  AGENTS.md
  STRUCTURE.md
  docs/
    API.md
    EVENTS.md
    IMPLEMENTATION.md
    DECISIONS.md
    REFERENCES.md
  packages/
    sdk/
      package.json
      src/
        index.ts
        agent.ts
        runner.ts
        run.ts
        events.ts
        result.ts
        policy.ts
        workspace.ts
        adapters/
          mock.ts
      tests/
        mock-run.test.ts
        event-order.test.ts
        result.test.ts
```

Goal:

- Create a run.
- Stream mock events.
- Resolve a result.
- Prove event order.

Nothing else.

---

## 3. v0.2: fake process adapter

```txt
packages/
  sdk/
    src/
      adapters/
        fake-process.ts
    tests/
      fake-process-adapter.test.ts
fixtures/
  fake-agent/
    emit-success.js
    emit-failure.js
    emit-approval.js
```

Goal:

- Spawn a child process.
- Parse newline-delimited JSON events.
- Convert process output into standard events.
- Handle non-zero exit as `run.failed`.

---

## 4. v0.3: ACP adapter

```txt
packages/
  adapters-acp/
    package.json
    src/
      index.ts
      acp-adapter.ts
      json-rpc.ts
      acp-event-normalizer.ts
      acp-types.ts
    tests/
      acp-adapter.test.ts
      acp-event-normalizer.test.ts
```

Goal:

- Hide ACP JSON-RPC behind `adapters.acp()`.
- Support initialize, session creation, prompt sending, updates, cancellation, and permission requests.
- Normalize raw ACP messages into standard events.

The app-facing SDK must not change.

---

## 5. v0.4: conductor and proxies

```txt
packages/
  adapters-conductor/
    src/
      index.ts
      conductor-adapter.ts
      conductor-config.ts
  proxies/
    policy/
      src/index.ts
    redaction/
      src/index.ts
    event-normalizer/
      src/index.ts
    diff-review/
      src/index.ts
```

Goal:

- Insert conductor/proxy chains behind the adapter layer.
- Support policy, redaction, event normalization, and diff review proxies.
- Keep the same public SDK API.

---

## 6. v0.5: local coding fixture

```txt
examples/
  buggy-calculator/
    package.json
    pnpm-lock.yaml
    src/
      add.ts
      add.test.ts

tests/
  fixtures/
    buggy-calculator.test.ts
```

Goal:

- Prove the SDK can produce code-work evidence.
- Expected evidence:
  - `changedFiles` contains `src/add.ts`
  - diff contains `return a + b`
  - `pnpm test` exits `0`

---

## 7. v1: local Docker sandbox

```txt
packages/
  sandbox-docker/
    src/
      index.ts
      docker-sandbox.ts
      workspace-mount.ts
      cleanup.ts
      sandbox-events.ts
    tests/
      docker-sandbox.test.ts
runtime/
  images/
    node-22.Dockerfile
```

Goal:

- Mount workspace.
- Run adapter inside container.
- Stream events to host.
- Clean up on completion/cancel/failure.
- Prove no platform key enters sandbox.

---

## 8. v1.2: Pi wrapper runtime

```txt
runtime/
  agent-runtime-pi/
    package.json
    src/
      main.ts
      run-contract.ts
      pi-session.ts
      event-bridge.ts
      result-writer.ts
      hooks/
        command-policy-hook.ts
        redaction-hook.ts
        event-stream-hook.ts
        approval-hook.ts
      skills/
        environment-detect.ts
        environment-build.ts
        environment-verify.ts
```

Goal:

- Treat Pi as an inner harness.
- Fetch run contract.
- Configure model/tools/hooks/skills/context explicitly.
- Subscribe to Pi events.
- Normalize to SDK event schema.
- Stream events back to controller or host.

---

## 9. v1.5: GitHub draft PR workflow

```txt
packages/
  github/
    src/
      github-app.ts
      installation-token.ts
      repo-checkout.ts
      branch.ts
      pull-request.ts
      git-events.ts
    tests/
      github-app.test.ts
      draft-pr.test.ts
```

Goal:

- Use GitHub App installation token.
- Clone repo.
- Create branch.
- Commit changes.
- Open draft PR.
- Return `result.pr.url`.

---

## 10. v2: environment setup subsystem

```txt
packages/
  environment/
    src/
      environment-profile.ts
      detective.ts
      builder.ts
      verifier.ts
      profile-staleness.ts
      profile-validation.ts
      environment-events.ts
    tests/
      detective.test.ts
      builder.test.ts
      verifier.test.ts
      staleness.test.ts
```

Goal:

- Turn an unknown repo into a verified workspace.
- Support detective, builder, verifier flows.
- Save environment profile with commands, services, secrets, validation status, confidence, and last verified commit.

---

## 11. v3: cloud control plane client

```txt
packages/
  client-cloud/
    src/
      background-agent-client.ts
      runs-api.ts
      events-sse.ts
      webhooks.ts
      api-key-auth.ts
    tests/
      runs-api.test.ts
      events-sse.test.ts
```

Goal:

- `new BackgroundAgent({ apiKey })`
- `client.runs.create()`
- `client.runs.events()`
- `client.runs.result()`
- webhook helpers

---

## 12. v4: React UI helpers

```txt
packages/
  react/
    src/
      useAgentRun.ts
      useRunEvents.ts
      useApprovals.ts
      useRunResult.ts
      components/
        EventTimeline.tsx
        ToolCard.tsx
        TerminalCard.tsx
        DiffCard.tsx
        ApprovalCard.tsx
        PRCard.tsx
```

Goal:

- Make the event stream easy to render.
- Support chat workspace, terminal cards, approval cards, diff review, PR cards.

---

## 13. Final mature monorepo

```txt
background-agent-sdk/
  README.md
  SOURCE.md
  AGENTS.md
  STRUCTURE.md
  package.json
  pnpm-workspace.yaml
  tsconfig.base.json
  docs/
    API.md
    EVENTS.md
    IMPLEMENTATION.md
    DECISIONS.md
    REFERENCES.md
    SECURITY.md
    ENVIRONMENT.md
    TESTING.md
  packages/
    sdk/
      src/
        index.ts
        agent.ts
        runner.ts
        run.ts
        events.ts
        result.ts
        policy.ts
        workspace.ts
        sandbox.ts
        query.ts
    core/
      src/
        event-bus.ts
        run-state-machine.ts
        result-collector.ts
        approval-store.ts
        redaction.ts
        trace.ts
    adapters-mock/
    adapters-process/
    adapters-acp/
    adapters-conductor/
    adapters-opencode/
    adapters-pi/
    sandbox-docker/
    environment/
    github/
    client-cloud/
    react/
    testing/
  runtime/
    agent-runtime-pi/
    images/
  proxies/
    policy/
    redaction/
    event-normalizer/
    diff-review/
    trace/
  examples/
    local-mock/
    fake-process/
    acp-opencode/
    conductor-proxy-chain/
    buggy-calculator/
    docker-run/
    github-draft-pr/
  fixtures/
    events/
    repos/
    secrets/
  tests/
    integration/
    e2e/
```

---

## 14. Structure rule

Every folder must earn its existence.

Create a new package only when:

- the mock SDK is already passing,
- the concern can be tested independently,
- the public API stays stable,
- docs describe the package boundary.

Do not create packages just because the final architecture will need them someday.
