"""
Offline eval gate — run this in CI before deploying a prompt/retrieval
config change. Requires: pip install ragas datasets

Usage:
    python eval/ragas_eval.py

It calls your running backend's /v1/chat endpoint for each golden question,
captures the answer + retrieved context (you'll need to expose retrieved
context via a debug flag or query the DB directly — see NOTE below), then
scores faithfulness / answer_relevancy / context_precision with RAGAS.

This is a starting skeleton, not a finished CI script — wire in your own
context-capture method before relying on it as a merge gate.
"""
import json
import sys
from pathlib import Path

import httpx

API_BASE = "http://localhost:8000"
GOLDEN_PATH = Path(__file__).parent / "golden_dataset.json"
FAITHFULNESS_THRESHOLD = 0.7
RELEVANCY_THRESHOLD = 0.7


def new_session_token() -> str:
    # server-issued sessions: /v1/chat rejects requests without a valid token.
    # (Needs TURNSTILE_SECRET unset on the server you evaluate against.)
    resp = httpx.post(f"{API_BASE}/v1/session", json={}, timeout=15)
    resp.raise_for_status()
    return resp.json()["token"]


def call_chat(question: str, token: str) -> str:
    resp = httpx.post(
        f"{API_BASE}/v1/chat",
        headers={"X-Session-Token": token},
        json={"message": question},
        timeout=30,
    )
    resp.raise_for_status()
    # SSE response — concatenate all data: lines
    answer = ""
    for line in resp.text.splitlines():
        if line.startswith("data: ") and line != "data: [DONE]":
            answer += line[6:].replace("\\n", "\n")
    return answer


def main() -> int:
    golden = json.loads(GOLDEN_PATH.read_text())

    try:
        from ragas import evaluate
        from ragas.metrics import faithfulness, answer_relevancy
        from datasets import Dataset
    except ImportError:
        print("Install eval deps first: pip install ragas datasets")
        return 1

    questions, answers, ground_truths, contexts = [], [], [], []
    for row in golden:
        answer = call_chat(row["question"], new_session_token())  # fresh session = no history bleed
        questions.append(row["question"])
        answers.append(answer)
        ground_truths.append(row["ground_truth"])
        # NOTE: contexts left empty here — wire this up to whatever your
        # retrieval step actually returned for this question (e.g. add a
        # debug=true flag to /v1/chat that also returns retrieved chunks).
        contexts.append([])

    dataset = Dataset.from_dict(
        {
            "question": questions,
            "answer": answers,
            "ground_truth": ground_truths,
            "contexts": contexts,
        }
    )

    result = evaluate(dataset, metrics=[faithfulness, answer_relevancy])
    print(result)

    scores = result.to_pandas()
    avg_faithfulness = scores["faithfulness"].mean()
    avg_relevancy = scores["answer_relevancy"].mean()

    passed = avg_faithfulness >= FAITHFULNESS_THRESHOLD and avg_relevancy >= RELEVANCY_THRESHOLD
    print(f"faithfulness={avg_faithfulness:.2f} relevancy={avg_relevancy:.2f} pass={passed}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
