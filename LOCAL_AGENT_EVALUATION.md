# Local agent evaluation

Measured on 2026-11-02 with Ollama `qwen3:14b-q4_K_M` on the user's Apple M4 Pro, 24 GB unified memory. These are single runs, not statistically meaningful benchmarks. The model server was already warm; times include CLI work and local inference. No cloud inference or production logs were used.

## Explorer

Question: “What does read_source do in token-graph/scripts/explore_agent.py? Cite source lines.” Both versions used the same current graph and source. The baseline was the repository's `HEAD` version of the explorer, with only its script directory redirected to the working tree. The source being explored already contained the ongoing changes.

| Measure | Baseline | Improved (before final minor evidence-display changes) |
| --- | ---: | ---: |
| Wall time | 26.21 s | 25.71 s |
| CLI maximum resident set | 62,750,720 bytes | 53,067,776 bytes |
| Model input tokens | unavailable | 1,519 + 2,584 across two calls (includes repeated context) |
| Model output tokens | unavailable | 33 + 184 |
| Report bytes | not recorded | 305 |
| Evidence result | Answered with line numbers; semantic accuracy not independently scored | Abstained: its model answer did not pass citation checks |

This case demonstrates a loss of answer coverage, not a measured accuracy improvement. Citation validation checks referenced file/line ranges, not whether a claim follows from the cited text. Neither prompt-injection resistance nor overall accuracy is established by a single example. Ollama reported a 10 GB loaded model footprint at 8,192 context; this is not a measurement of peak GPU or unified memory. CLI RSS excludes the Ollama server.

After citation-format normalization and a bounded correction pass, a repeat of the same question returned a source-cited answer in 16.12 seconds (two local calls, 1,551 + 2,584 input and 33 + 228 output tokens; 883 report bytes). The cited definition and tool description matched the current source. This is one warm-model trial and is not a general accuracy or speed comparison. A valid citation still does not prove a claim.

## Log analyzer

A four-event synthetic fixture with two repeated database connection refusals and a timeout produced correct deterministic counts (2 and 1) and the shared trace across two services. A single local interpretation took 25.33 s wall time, reported 631 input and 538 output model tokens, and the CLI reached 33,554,432 bytes maximum RSS (excluding Ollama). The example credential was redacted before inference. The causal relationship remained explicitly uncertain. These figures do not establish real-world accuracy or peak model memory.

A 50,000-line synthetic repeated-error file (3,350,000 bytes) was analyzed deterministically in 2.12 s, yielding 50,000 events and one error group; the report was 3,001 bytes. This is a byte-size observation for an unusually repetitive fixture, not measured cloud-token savings. The sandbox prevented `/usr/bin/time` from reporting peak memory for this run.

No direct Codex or Claude log-analysis comparison was run, and cloud context consumption or savings have not been measured. No peak Ollama memory was measured. Comparative accuracy, per-task evidence precision, prompt-injection resistance against adversarial models, and throughput require a representative labeled evaluation set and repeated controlled runs.
