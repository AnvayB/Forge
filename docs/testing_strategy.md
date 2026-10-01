# Testing strategy

Forge's offline test suite protects user-visible behavior and data invariants across five layers. Line coverage is a discovery aid; a high percentage does not replace a test that proves a critical workflow still works.

## Layers

1. **Unit:** Pure analytics, routing, parsing, formatting, and message chunking. Keep inputs explicit and assert decisions and boundary behavior.
2. **Integration:** Real services, repositories, configuration, and temporary SQLite databases. Verify commit/reopen persistence, rollback, workout history, and schema upgrades across independent sessions.
3. **Discord workflow:** Build the real bot, invoke its registered callbacks/events with small context and attachment doubles, and assert both Discord replies and durable/file side effects. Never connect to Discord in the normal suite.
4. **Regression:** Preserve a focused reproducer for each fixed user-facing failure. At minimum, retain the real nested workout-set progress case, free-text error reply, multi-image workout merge, workout confirmation formatting, and saved-review redelivery checks.
5. **Smoke:** Exercise offline startup boundaries: API construction and health/event routes with a temporary database, plus registration of critical Discord commands. No API key, production database, or Discord token is required.

OpenAI SDK calls may use fakes at the client boundary to verify request shape, tool chaining, file lifecycle, and graceful offline behavior. Keep the business logic, persistence, and routing real beneath that boundary. Any optional live-provider check belongs in a separate opt-in job with explicit credentials and must not gate offline developer feedback.

## Test-first workflow

For a bug fix, add the smallest test that reproduces the reported failure and confirm it fails before changing production code. For a feature, first add the acceptance case for the behavior a user should observe, then add unit cases for important boundaries. Implement the change, run the focused test, then run the complete offline suite and smoke layer. Keep each historical reproducer in the suite after the fix; update it only when the behavior intentionally changes.

Run the complete offline suite with:

```bash
pytest
```

Run one layer while iterating with:

```bash
pytest tests/bot
pytest tests/integration
```
