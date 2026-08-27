/* Payloads the dashboard's live readers are fed, including the empty ones.
 *
 * These exist because `render_check.js` makes every `fetch` reject, so it
 * exercises the *mock* path of every reader and none of the live path — and
 * the live path is where the shaping lives. `API.sirvis.runtimeSet` alone was
 * cyclomatic complexity 30, essentially all of it optional-field handling, and
 * not one of those branches had ever been executed by a check.
 *
 * **The sparse fixture matters more than the full one.** Every `||{}` and
 * `||[]` in a reader is a claim about a field SIRVIS might not send. The full
 * payload proves the happy path; the sparse payload is the only thing that
 * proves those defaults do what their author believed.
 */

/* A complete interaction matrix, of the shape SIRVIS M11 publishes. */
const RUNTIME_SETS = {
  items: [{ runtime_set_id: "rs_balanced", name: "clarvis-balanced", revision: 3 }],
};

function result(role, family, format, quant, extra = {}) {
  return {
    target_key: role,
    target: { model_family: family, format, quantization: quant },
    evidence_type: "MEASURED",
    validity: "VALID",
    validity_notes: ["ambient load below 5%"],
    interaction_matrix: {
      runtime_set: "clarvis-balanced",
      revision: 3,
      complete: true,
      co_residency_failure: null,
      basis: "three repetitions per condition",
      conditions: ["alone", "sequential", "concurrent"],
      load_order: ["agent", "chat"],
      memory: {
        concurrent: { lowest_available_bytes: 8_589_934_592, swap_used_bytes: 0 },
      },
      rows: {
        agent: {
          figures: {
            alone: { tokens_per_second: 29.53, time_to_first_token: 0.2612, samples: 3 },
            sequential: { tokens_per_second: 28.11, time_to_first_token: 0.2701, samples: 3 },
            concurrent: { tokens_per_second: 19.04, time_to_first_token: 0.4413, samples: 3 },
          },
          degradation_percent: { concurrent: { tokens_per_second: 35.52 } },
        },
        chat: {
          figures: {
            alone: { tokens_per_second: 25.31, time_to_first_token: 0.2244, samples: 3 },
            sequential: { tokens_per_second: 24.02, time_to_first_token: 0.2299, samples: 3 },
            concurrent: { tokens_per_second: 16.88, time_to_first_token: 0.5017, samples: 3 },
          },
          degradation_percent: { concurrent: { tokens_per_second: 33.31 } },
        },
      },
      ...extra,
    },
  };
}

const FULL_RUNS = {
  items: [{
    run_id: "run_1",
    results: [
      result("agent", "qwen2.5-coder-7b-instruct", "mlx", "4bit"),
      result("chat", "ministral-8b-instruct-2410", "mlx", "4bit"),
    ],
  }],
};

/* The same shape with every optional field absent. Each omission here is one
   `||` in the reader, executed for the first time. */
const SPARSE_RUNS = {
  items: [{
    run_id: "run_2",
    results: [{
      target_key: "agent",
      target: { model_family: "solo-model" },
      interaction_matrix: {
        runtime_set: "clarvis-balanced",
        revision: 1,
        rows: { agent: {} },
      },
    }],
  }],
};

/* A run with no interaction matrix at all — the reader must fall to its mock
   rather than shaping an empty measurement into a confident-looking table. */
const NO_MATRIX_RUNS = {
  items: [{ run_id: "run_3", results: [{ target_key: "agent", target: {} }] }],
};

/* A co-residency failure, because `complete: false` takes a different branch in
   the validity block and prints the failure reason into the view. */
const FAILED_RUNS = {
  items: [{
    run_id: "run_4",
    results: [result("agent", "big-model", "gguf", "q8", {
      complete: false,
      co_residency_failure: "chat evicted agent at 31.2 GB",
    })],
  }],
};

const CASES = [
  { name: "full", sets: RUNTIME_SETS, runs: FULL_RUNS },
  { name: "sparse", sets: RUNTIME_SETS, runs: SPARSE_RUNS },
  { name: "no-matrix", sets: RUNTIME_SETS, runs: NO_MATRIX_RUNS },
  { name: "co-residency-failure", sets: RUNTIME_SETS, runs: FAILED_RUNS },
  { name: "no-sets", sets: { items: [] }, runs: FULL_RUNS },
];

/* Recorded SSE traffic, of the shape RAVIS relays and NERVIS forwards unchanged.
 *
 * `sendChat` measured cyclomatic complexity 30 and had no coverage of any kind,
 * because exercising it end to end needs a running model — which is exactly the
 * reason its frame handling is now a pure function taking a frame and an
 * accumulator. These are the cases that function has to get right, and none of
 * them needs a model to run.
 */
function delta(content, extra = {}) {
  return { frame: { choices: [{ delta: { content } }], ...extra } };
}

const CHAT_STREAMS = [
  {
    name: "ordinary reply",
    frames: [
      delta("Hello", { model: "qwen2.5-coder-7b" }),
      delta(", world"),
      { frame: { choices: [{ delta: {} }], model: "qwen2.5-coder-7b" } },
    ],
  },
  {
    /* The case `replyMessage` exists for: a reasoning model that spent its whole
       budget thinking. An empty reply is almost never an empty reply. */
    name: "reasoning only",
    frames: [
      { frame: { choices: [{ delta: { reasoning_content: "..." } }] } },
      { frame: { choices: [{ delta: { reasoning_content: "..." } }] } },
    ],
  },
  {
    /* A refusal arrives as an error marker followed by the frame carrying the
       reason, so the reason is read only once the marker has been seen. */
    name: "refused mid-stream",
    frames: [delta("partial "), { error: true }, { frame: { message: "no upstream available" } }],
  },
  {
    name: "refused with no reason given",
    frames: [{ error: true }, { frame: {} }],
  },
  {
    /* Frames that are legal SSE and carry nothing the shape expects. Every
       `||{}` in the delta walk is one of these. */
    name: "malformed frames",
    frames: [
      { frame: {} },
      { frame: { choices: [] } },
      { frame: { choices: [null] } },
      { frame: { choices: [{}] } },
      delta("survived"),
    ],
  },
];

/* Model builds, for the detail panel `BUILD.render` draws.
 *
 * It measured cyclomatic complexity 28 and `render_check.js` never calls it —
 * that check walks screens, and this panel only appears once a build is
 * selected. It is a pure function of its three arguments, so pinning it costs
 * a fixture and covers the whole refactor.
 *
 * A measured field arrives either bare or wrapped with its provenance and both
 * spellings are live in SIRVIS's payloads, so both appear here.
 */
const BUILDS = [
  {
    name: "fully described, loaded, held by this dashboard",
    model: {
      runtime_key: "qwen2.5-coder-7b-instruct-mlx-4bit",
      display_name: "Qwen2.5 Coder 7B",
      is_loaded: true,
      family: {
        display_name: "Qwen2.5 Coder",
        architecture: { value: "qwen2", provenance: "config.json" },
        parameter_billions: { value: 7.6, provenance: "config.json" },
      },
      variant: { runtime_format: "mlx", quantization: "4bit", publisher: "mlx-community" },
      declared_context: { value: 32768, provenance: "model card" },
      instances: [{ effective_context: 8192 }],
    },
    evidence: { items: [{ role: "chat" }, { role: "chat" }, { role: "agent" }] },
    residency: { leases: [{ session_id: "s_1", owner: "nervis-dashboard", models: ["qwen2.5-coder-7b-instruct-mlx-4bit"] }] },
  },
  {
    /* Every optional field absent — each one is an em-dash fallback that had
       never been executed by a check. */
    name: "bare build, nothing known, nothing resident",
    model: { runtime_key: "mystery-build" },
    evidence: null,
    residency: null,
  },
  {
    name: "architecture disagrees, no evidence for this packaging",
    model: {
      runtime_key: "granite-3b-gguf-q4",
      display_name: "Granite 3B",
      is_loaded: false,
      family: { architecture: "granite" },
      variant: { runtime_format: "gguf", quantization: "Q4_K_M", architecture: "llama",
                 family_architecture_disagrees: true },
      declared_context: 4096,
    },
    evidence: { items: [] },
    residency: { leases: [] },
  },
  {
    /* Held by somebody else, and by a lease that bundles several models — the
       two warnings a reader must see before clicking Release. */
    name: "held elsewhere, by a bundled lease",
    model: { runtime_key: "shared-model", display_name: "Shared", is_loaded: true,
             instances: [{ effective_context: 16384 }, {}] },
    evidence: { items: [{ role: "agent" }] },
    residency: { leases: [
      { session_id: "s_2", owner: "nervis-dashboard", models: ["shared-model", "other-model"] },
      { session_id: "s_3", owner: "clarvis", models: ["shared-model"] },
      { session_id: "s_4", owner: null, models: ["shared-model"] },
    ] },
  },
];

/* RAVIS management payloads, for the readers that only run when a service
 * answers.
 *
 * **`render_check` cannot see these.** It makes every `fetch` reject, which is
 * the state it exists to test — and which means it exercises the *mock* branch
 * of every live-first reader and never the live one. A reader that breaks only
 * when real data arrives is invisible to it, and one did: the Providers screen
 * read `x.auth.scheme` on a live payload that carries no `auth` at all, and
 * threw the first time a provider actually answered.
 *
 * Each fixture is the shape RAVIS really returns, trimmed. The fields that
 * matter are the ones the live payload does *not* have, because those are what
 * the mock taught the screens to expect.
 */
const RAVIS_PROVIDERS = {
  items: [
    {
      name: "lmstudio", base_url: "http://127.0.0.1:1234", local: true,
      enabled: true, credential_configured: false, credential_source: "absent",
      protocol_mode: "OPENAI_TRANSPARENT", reachable: true, latency_ms: 31.4,
      catalogue_size: 20, catalogue_error: "", catalogue_refreshed: 1.0,
      // Called, so it has a record.
      breaker: "CLOSED", error_rate: 0.0, requests: 4, consecutive_failures: 0,
    },
    {
      name: "openrouter", base_url: "https://openrouter.ai/api", local: false,
      enabled: true, credential_configured: true, credential_source: "file",
      protocol_mode: "OPENAI_TRANSPARENT", reachable: true, latency_ms: 988.0,
      catalogue_size: 417, catalogue_error: "", catalogue_refreshed: 1.0,
      // Never called: null rather than a clean bill of health.
      breaker: null, error_rate: null, requests: 0, consecutive_failures: 0,
    },
    {
      name: "anthropic", base_url: "", local: false, enabled: true,
      credential_configured: true, credential_source: "file",
      protocol_mode: "TRANSLATED_NATIVE", reachable: false,
      detail: "not answering", latency_ms: null, catalogue_size: null,
      catalogue_error: "HTTP 503", breaker: "OPEN", error_rate: 0.75,
      requests: 8, consecutive_failures: 3,
    },
  ],
};

/* One decision record, with the fields the Routes screen renders and the ones
   it used to invent. There is no `score`, no per-candidate `ttft_s` and no
   `timings_ms` — RAVIS publishes none of them. */
const RAVIS_DECISIONS = {
  items: [
    {
      decision_id: "9b3d0cd81850aa", requested: "ravis/balanced",
      pool: "ravis/balanced", selected: "google/gemma-4-31b-it:free",
      fallbacks: ["nvidia/nemotron-3-super-120b-a12b:free", "prism-ml/bonsai-27b"],
      reason: "first eligible candidate in stable order.",
      requirements: ["none"], unverified: [],
      considered: ["a/one", "a/two", "b/three"],
      excluded: [
        { model: "a/two", reasons: ["outside this pool's default mid tier — tick it to include it"] },
        { model: "b/three", reasons: ["outside this pool's default mid tier — tick it to include it"] },
      ],
      execution: {
        provider: "openrouter",
        attempts: [
          { model: "google/gemma-4-31b-it:free", outcome: "rate_limit", detail: "HTTP 429" },
          { model: "nvidia/nemotron-3-super-120b-a12b:free", outcome: "succeeded", detail: "" },
        ],
        stopped_because: "", budget_unenforced: [],
      },
      execution_path: "TRANSPARENT_OPENAI",
      decided_at: "2026-08-27T00:14:02Z", application_id: "anonymous",
      request_id: "req_1", trace_id: "",
    },
    {
      decision_id: "no-route-01", requested: "ravis/local", pool: "ravis/local",
      selected: null, fallbacks: [], reason: "no candidate satisfies ravis/local",
      requirements: [], unverified: [], considered: ["a/one"],
      excluded: [{ model: "a/one", reasons: ["served by a remote provider, and this pool never leaves this machine"] }],
      execution: { provider: "", attempts: [], stopped_because: "", budget_unenforced: [] },
      execution_path: "TRANSPARENT_OPENAI", decided_at: "2026-08-27T00:13:00Z",
      application_id: "anonymous", request_id: "req_2", trace_id: "",
    },
  ],
};

/* Measured timings, including one below the sample floor — the case a screen
   must show and never rank on. */
const RAVIS_OBSERVATIONS = {
  items: [
    { model_id: "gpt-4o-mini", median_latency_ms: 565.0, median_ttft_ms: 484.0,
      samples: 14, confident: true, last_seen: 1.0, provenance: "OBSERVED_BY_RAVIS" },
    { model_id: "claude-haiku-4-5", median_latency_ms: 900.0, median_ttft_ms: null,
      samples: 1, confident: false, last_seen: 1.0, provenance: "OBSERVED_BY_RAVIS" },
  ],
  minimum_samples: 5,
  confident_total: 1,
};

const RAVIS_READS = [
  { name: "providers", path: "/api/v1/providers", body: RAVIS_PROVIDERS,
    call: (api) => api.ravis.providers() },
  { name: "decisions", path: "/api/v1/route-decisions", body: RAVIS_DECISIONS,
    call: (api) => api.ravis.decisions(25) },
  { name: "observations", path: "/api/v1/observations", body: RAVIS_OBSERVATIONS,
    call: (api) => api.ravis.observations() },
];

module.exports = { CASES, CHAT_STREAMS, BUILDS, RAVIS_READS };
