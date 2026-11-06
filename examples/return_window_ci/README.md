# Return-window CI reference

Check that a consuming repository can run a pinned AugmentWorks Action and identify a policy regression by name. The test asks a fictional store how long a customer has to return an order. The agent formats one canonical sentence using the number in `window-days.txt` at startup. That file belongs to the checked-out build. The independently versioned expectation in `packet.json` stays at thirty days across both build revisions.

| Days in the checked-out build | Expected CLI exit | Result for `return-window-30-days` |
| --- | --- | --- |
| 7 | 10 | Failed |
| 30 | 0 | Passed |

The [caller workflow](../../.github/workflows/return-window.yml) starts the checked-out code, pins the external Action by full commit SHA, and records the tested commit, local run ID and policy result in the job summary.

The fixture runs on loopback with synthetic data. The response-only packet looks for the full, case-sensitive sentence emitted by this fixture. It exercises version binding, result reporting and failure detection for these two deterministic builds. Arbitrary natural-language answers, hosted credits, dashboard report links and GitHub App checks require separate tests.

## Reproduce one revision locally

Use Python 3.10+ and Node 22.12+ with npm. From the repository root in Bash:

```bash
cd examples/return_window_ci
export CHATBOT_API_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(16))')"
export CHATBOT_BASE_URL=http://127.0.0.1:8197
out="$(mktemp -d)/report"
python3 server.py --port 8197 &
fixture_pid=$!
trap 'kill "$fixture_pid" 2>/dev/null || true' EXIT
npx --yes @augmentworks/cli@0.4.2 test --local \
  --config augmentworks.yaml --packet packet.json --output-dir "$out"
echo "CLI exit: $?"
echo "Open $out/report.html"
kill "$fixture_pid" 2>/dev/null || true
trap - EXIT
```

The first npm fetch requires network access. The agent and evaluation run locally. Give each revision its own checkout, process and report directory. Keep the packet unchanged between runs. The JSON report records the named assertion and the response seen by the runner.
