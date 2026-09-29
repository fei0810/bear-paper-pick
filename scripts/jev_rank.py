#!/usr/bin/env python3
"""Bear Sift's bounded Jev adapter.

The adapter accepts only public bibliographic metadata and English abstracts. It
does not fetch full text, generate a prose recommendation, or mutate the
candidate fact layer. Calls create append-only rank records for shadow/review
workflows; code, not Jev, controls final selection.

Remote state per candidate = REMOTE_CARD_FIELDS of the candidate plus the
profile's positive/negative axes, logical form and language. Channel names,
seed IDs, layer targets, line names and local paths never leave the machine.
Ineligible candidates (no abstract, not marked English, too long) are recorded
under "skipped" and do not stop the run. Answers outside the offered options or
ranges are flagged and never cached. In review mode records are ordered by a
code-computed priority; shadow mode keeps input order.

Usage:
  jev_rank.py smoke [--out-dir DIR] [--env-file FILE] [--timeout SECONDS]
  jev_rank.py rank --profile PROFILE.json --candidates candidates.json
                   [--out DIR] [--limit N] [--env-file FILE] [--timeout SECONDS]

--limit counts candidates actually sent, after ineligible ones are skipped.
Exit codes: 0 ok; 3 TypeSafe failed after retries; 64 configuration error;
65 input file or data validation error. A failed rank still writes the records
it finished as a "failed_partial" run.
"""
import argparse
import datetime as dt
import hashlib
import json
import math
import os
import re
import socket
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from profile_snapshot import read_profile, validate_profile  # noqa: E402

API_BASE = "https://api.typesafe.ai/v1"
DEFAULT_MODEL = "jev-1.13.0"
DEFAULT_MODE = "review"
ALLOWED_SCOPE = "public_metadata_and_english_abstract_only"
QUESTION_SET_VERSION = "paper-radar-jev-v1"
RANK_RUN_SCHEMA = "paper-radar-jev-rank-run-v3"
MAX_ABSTRACT_CHARS = 24000
# 429 限流、529 过载为官方文档列出的可重试错误；5xx 网关错误同样是瞬时故障。
RETRYABLE_STATUS = {429, 500, 502, 503, 504, 529}
# 只有这些候选字段会离开本机；channel、cites_seed、hard_risk 只留在本地审计记录。
REMOTE_CARD_FIELDS = ("id", "doi", "title", "year", "venue", "abstract", "abstract_language", "abstract_sentences")


from config import ConfigError, read_dotenv


class ApiError(RuntimeError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def utc_now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def json_fingerprint(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def write_json(value, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".paper-radar-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temp_path, path)
    except Exception:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass
        raise


def load_config(env_file=None, environ=None):
    environ = os.environ if environ is None else environ
    env_file = Path(env_file or environ.get("BEAR_SIFT_ENV_FILE") or Path(__file__).resolve().parents[1] / ".env")
    file_values = read_dotenv(env_file)

    def choose(name, default=""):
        return environ.get(name) or file_values.get(name) or default

    key = choose("TYPESAFE_API_KEY")
    if not key.strip():
        raise ConfigError(f"未找到 TYPESAFE_API_KEY。请在 {env_file} 中设置，或显式提供环境变量。")
    model = choose("JEV_MODEL", DEFAULT_MODEL).strip()
    mode = choose("JEV_MODE", DEFAULT_MODE).strip()
    scope = choose("JEV_EXTERNAL_SEND_SCOPE", ALLOWED_SCOPE).strip()
    if mode not in {"shadow", "review"}:
        raise ConfigError("JEV_MODE 目前只允许 shadow 或 review；apply 需待独立校准后开放")
    if scope != ALLOWED_SCOPE:
        raise ConfigError("首发只允许 JEV_EXTERNAL_SEND_SCOPE=public_metadata_and_english_abstract_only")
    if not model:
        raise ConfigError("JEV_MODEL 不能为空")
    raw_confidence = choose("JEV_MIN_CONFIDENCE").strip()
    min_confidence = None
    if raw_confidence:
        try:
            min_confidence = float(raw_confidence)
        except ValueError:
            raise ConfigError("JEV_MIN_CONFIDENCE 必须是 0 到 1 之间的数") from None
        if not 0 <= min_confidence <= 1:
            raise ConfigError("JEV_MIN_CONFIDENCE 必须是 0 到 1 之间的数")
    return {"api_key": key, "model": model, "mode": mode, "scope": scope,
            "min_confidence": min_confidence, "env_file": str(env_file)}


class JevClient:
    def __init__(self, config, timeout=40, retries=2, opener=None, sleeper=None):
        self.config = config
        self.timeout = timeout
        self.retries = retries
        self.opener = opener or urllib.request.urlopen
        self.sleeper = sleeper or time.sleep

    def request(self, method, path, body=None):
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        headers = {"Authorization": f"Bearer {self.config['api_key']}", "Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(API_BASE + path, data=data, headers=headers, method=method)
        attempts = 0
        started = time.monotonic()
        while True:
            try:
                with self.opener(request, timeout=self.timeout) as response:
                    payload = json.load(response)
                return payload, {"attempts": attempts + 1, "latency_ms": round((time.monotonic() - started) * 1000)}
            except urllib.error.HTTPError as error:
                attempts += 1
                if error.code not in RETRYABLE_STATUS or attempts > self.retries:
                    raise ApiError(error.code, f"TypeSafe API 返回 HTTP {error.code}") from error
                retry_after = error.headers.get("Retry-After") if error.headers else None
                try:
                    delay = max(0, min(float(retry_after), 10)) if retry_after else min(2 ** (attempts - 1), 10)
                except ValueError:
                    delay = min(2 ** (attempts - 1), 10)
                self.sleeper(delay)
            except (socket.timeout, TimeoutError, urllib.error.URLError, ConnectionError) as error:
                attempts += 1
                if attempts > self.retries:
                    raise ApiError(None, "TypeSafe API 在有限重试后仍无法连接或超时") from error
                self.sleeper(min(2 ** (attempts - 1), 10))
            except (json.JSONDecodeError, OSError) as error:
                raise ApiError(None, "TypeSafe API 的响应无法解析") from error

    def list_models(self):
        return self.request("GET", "/models")

    def evaluate(self, state, questions):
        body = {"state": state, "model": self.config["model"], "questions": questions}
        return self.request("POST", "/systemone", body)


def validate_answer_response(response, question_ids):
    if not isinstance(response, dict) or not isinstance(response.get("model"), str):
        raise ValueError("Jev 响应缺少 model")
    answers = response.get("answers")
    usage = response.get("usage")
    if not isinstance(answers, dict) or not isinstance(usage, dict):
        raise ValueError("Jev 响应缺少 answers 或 usage")
    missing = [question_id for question_id in question_ids if question_id not in answers]
    if missing:
        raise ValueError("Jev 响应缺少问题答案：" + ", ".join(missing))
    return response


def smoke_state_and_questions():
    state = {
        "data_scope": ALLOWED_SCOPE,
        "candidate": {
            "id": "paper-radar-smoke-abstract",
            "title": "Synthetic public test record",
            "abstract": "This English test abstract describes a reproducible screening workflow. It contains no user material, full text, or private data.",
        },
    }
    questions = {
        "english_abstract_status": {
            "type": "choice",
            "instructions": "Is `candidate.abstract` usable as an English abstract for preliminary semantic triage? Do not infer any scientific fact beyond the provided text.",
            "criteria": {
                "english_usable": "It is clearly English and contains enough text for a bounded abstract-level triage.",
                "not_usable": "It is missing, not English, or too limited for abstract-level triage.",
            },
        }
    }
    return state, questions


def audit_path(out_dir, prefix):
    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return Path(out_dir) / "runs" / f"{prefix}-{timestamp}-{uuid.uuid4().hex[:8]}.json"


def run_smoke(config, out_dir, client=None):
    client = client or JevClient(config)
    state, questions = smoke_state_and_questions()
    models, models_meta = client.list_models()
    response, eval_meta = client.evaluate(state, questions)
    validate_answer_response(response, questions.keys())
    problems = answer_problems(response["answers"], questions)
    if problems or response["model"] != config["model"]:
        raise ValueError("smoke 响应不满足固定模型与 typed answer 契约")
    record = {
        "schema_version": "paper-radar-jev-smoke-v1",
        "run_id": str(uuid.uuid4()),
        "created_at": utc_now(),
        "configuration": {
            "model_requested": config["model"],
            "mode": config["mode"],
            "external_send_scope": config["scope"],
            "credential_source": "environment" if os.environ.get("TYPESAFE_API_KEY") else "dotenv",
        },
        "input_fingerprint": json_fingerprint({"state": state, "questions": questions, "model": config["model"]}),
        "sent_scope": "synthetic_english_abstract_only",
        "models_endpoint": {
            "status": "ok",
            "model_names": [item.get("name") for item in models.get("models", []) if isinstance(item, dict) and isinstance(item.get("name"), str)],
            "transport": models_meta,
        },
        "result": {"model": response["model"], "answers": response["answers"], "usage": response["usage"], "transport": eval_meta},
    }
    path = audit_path(out_dir, "smoke")
    write_json(record, path)
    return record, path


def split_sentences(abstract):
    chunks = re.split(r"(?<=[.!?])\s+", abstract.strip())
    return [chunk.strip() for chunk in chunks if chunk.strip()]


def eligibility(candidate):
    """返回不发送的原因列表；空列表表示可以发送。"""
    if not isinstance(candidate, dict):
        return ["not_an_object"]
    abstract = str(candidate.get("abstract") or "").strip()
    reasons = []
    if not str(candidate.get("id") or "").strip() or not str(candidate.get("title") or "").strip():
        reasons.append("missing_id_or_title")
    if not abstract:
        reasons.append("no_abstract")
    if str(candidate.get("abstract_language") or "").lower().strip() != "en":
        reasons.append("abstract_language_not_en")
    if len(abstract) > MAX_ABSTRACT_CHARS:
        reasons.append("abstract_too_long")
    if len(split_sentences(abstract)) > 254:
        reasons.append("too_many_sentence_options")
    if abstract and not split_sentences(abstract):
        reasons.append("no_usable_sentence")
    return reasons


def remote_card(card):
    """只有公开书目字段离开本机。"""
    return {key: card[key] for key in REMOTE_CARD_FIELDS if key in card}


def public_candidate_card(candidate):
    if not isinstance(candidate, dict):
        raise ValueError("候选必须为 JSON 对象")
    paper_id = str(candidate.get("id") or "").strip()
    title = str(candidate.get("title") or "").strip()
    abstract = str(candidate.get("abstract") or "").strip()
    abstract_language = str(candidate.get("abstract_language") or "").lower().strip()
    if not paper_id or not title or not abstract:
        raise ValueError("候选必须有 id、title 与英文 abstract")
    if abstract_language != "en":
        raise ValueError(f"候选 {paper_id} 必须显式标记 abstract_language=en，避免将非英文摘要发送给 Jev")
    if len(abstract) > MAX_ABSTRACT_CHARS:
        raise ValueError(f"候选 {paper_id} 的 abstract 超过 {MAX_ABSTRACT_CHARS} 字符上限")
    sentences = split_sentences(abstract)
    if not sentences:
        raise ValueError(f"候选 {paper_id} 的 abstract 无可用句子")
    return {
        "id": paper_id,
        "doi": str(candidate.get("doi") or "").strip() or None,
        "title": title,
        "year": candidate.get("year"),
        "venue": candidate.get("venue"),
        "abstract": abstract,
        "abstract_language": abstract_language,
        "abstract_sentences": [{"id": f"s{index + 1}", "text": sentence} for index, sentence in enumerate(sentences)],
        "channel": candidate.get("channel"),
        "cites_seed": candidate.get("cites_seed"),
        "hard_risk": "missing_or_unverified_public_metadata" if not candidate.get("doi") else None,
    }


def questions_for(profile, card):
    axis_criteria = {axis["id"]: axis["description"] for axis in profile["positive_axes"]}
    axis_criteria["none"] = "No positive profile axis is supported by the provided title and abstract."
    sentences = {sentence["id"]: sentence["text"] for sentence in card["abstract_sentences"]}
    sentences["none"] = "No sentence directly supports a bounded recommendation at abstract level."
    negative = profile["negative_axes"] or [{"id": "no_negative_axis", "description": "No negative axes are configured."}]
    return {
        "layer": {
            "type": "choice",
            "instructions": "Classify the candidate's role against `profile` using only the public title and English abstract in `candidate`. Choose insufficient whenever the abstract cannot support the judgment.",
            "criteria": {
                "core": "Directly advances the profile's central research question, object, or method.",
                "adjacent": "Useful neighboring method, evidence, or framing without being a direct match.",
                "cross_domain": "A potentially transferable research structure from another domain; it requires later human review.",
                "canonical": "A foundational or historical work useful for context, not a current direct match.",
                "exclude": "Outside the profile or contradicted by its negative axes.",
                "insufficient": "The title and abstract do not support a bounded layer judgment.",
            },
        },
        "fit": {
            "type": "score",
            "instructions": "Rate direct fit to `profile` using only `candidate`. This is not a paper-quality score.",
            "criteria": [
                "0: no supported fit or insufficient information",
                "1: weak or indirect fit",
                "2: clear useful fit",
                "3: direct, high-value fit to a defined positive axis",
            ],
        },
        "match_axis": {
            "type": "choice",
            "instructions": "Which one positive profile axis is best supported by `candidate`? Choose none when no axis is supported.",
            "criteria": axis_criteria,
        },
        "negative_conflict": {
            "type": "noul",
            "instructions": {"question": "Does `candidate` materially conflict with any configured negative profile axis?", "negative_axes": negative},
            "criteria": {"true": "A stated negative axis is materially implicated.", "false": "No material negative-axis conflict is supported by the abstract."},
        },
        "evidence_readiness": {
            "type": "choice",
            "instructions": "Is the English abstract sufficient for a limited recommendation rationale? This does not verify methods, results, identities, or factual claims.",
            "criteria": {
                "abstract_enough": "The abstract supports only a bounded relevance statement.",
                "needs_full_text": "The candidate may be relevant, but the required rationale needs source text beyond the abstract.",
                "insufficient": "The abstract does not support a relevance statement.",
            },
        },
        "support_sentence": {
            "type": "choice",
            "instructions": "Choose the one sentence from `candidate.abstract_sentences` that best supports the bounded relevance decision. Choose none if no sentence does.",
            "criteria": sentences,
        },
        "analogy_status": {
            "type": "choice",
            "instructions": "Assess only whether `candidate` exposes a potentially transferable research structure. This never authorizes automatic cross-domain recommendation.",
            "criteria": {
                "direct": "A comparable structure is directly visible in the abstract.",
                "adapt": "There may be an analogy, but domain adaptation is essential.",
                "not_analogy": "No comparable structure is supported.",
                "insufficient": "The abstract cannot support this judgment.",
            },
        },
        "transfer_boundary": {
            "type": "choice",
            "instructions": "If an analogy exists, which transfer boundary is supported by the abstract? This is a review routing signal, not a factual conclusion.",
            "criteria": {
                "direct_borrow": "The abstract supports considering a directly analogous approach.",
                "needs_adaptation": "The abstract supports an analogy but leaves material adaptation work.",
                "do_not_transfer": "The abstract does not support transfer.",
                "insufficient": "The abstract cannot support a transfer boundary.",
            },
        },
    }


def remote_profile_context(profile):
    """Keep line names, seeds, layer targets and local history out of the remote request."""
    return {
        "positive_axes": profile["positive_axes"],
        "negative_axes": profile["negative_axes"],
        "logical_form": profile["logical_form"],
        "language": profile["language"],
    }


def finite_number(value, low, high):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) and low <= value <= high


def answer_problems(answers, questions):
    """Validate every quantity used by ranking, not just the selected label."""
    # Live API rounds probabilities and scores to two decimals.
    # The bounds below propagate half a last-place unit, not a relevance threshold.
    problems = []
    for question_id, question in questions.items():
        answer = answers.get(question_id)
        if not isinstance(answer, dict):
            problems.append(f"{question_id}:missing")
            continue
        kind = question["type"]
        if answer.get("type") != kind:
            problems.append(f"{question_id}:wrong_type")
        if kind == "choice" and answer.get("choice") not in question["criteria"]:
            problems.append(f"{question_id}:out_of_options")
        if kind == "score" and not finite_number(answer.get("score"), 0, len(question["criteria"]) - 1):
            problems.append(f"{question_id}:score_out_of_range")
        if kind == "noul" and not finite_number(answer.get("noul"), 0, 1):
            problems.append(f"{question_id}:noul_out_of_range")
        if kind in {"choice", "score"}:
            if not finite_number(answer.get("confidence"), 0, 1):
                problems.append(f"{question_id}:invalid_confidence")
            probs = answer.get("probabilities")
            keys = set(question["criteria"]) if kind == "choice" else {str(i) for i in range(len(question["criteria"]))}
            if not isinstance(probs, dict) or set(probs) != keys or not all(finite_number(v, 0, 1) for v in probs.values()) or abs(sum(probs.values()) - 1) > min(0.05, 0.005 * len(keys) + 1e-9):
                problems.append(f"{question_id}:invalid_distribution")
            elif kind == "choice" and answer.get("choice") in probs and probs[answer["choice"]] + 0.010000001 < max(probs.values()):
                problems.append(f"{question_id}:choice_not_maximum")
            elif kind == "score" and finite_number(answer.get("score"), 0, len(keys) - 1) and abs(answer["score"] - sum(int(k) * v for k, v in probs.items())) > 0.005 * (1 + sum(range(len(keys)))) + 1e-9:
                problems.append(f"{question_id}:score_distribution_mismatch")
    return problems


def cache_path(out_dir, fingerprint):
    return Path(out_dir) / "cache" / f"{fingerprint}.json"


def cached_or_evaluate(client, out_dir, state, questions):
    """返回 (response, transport, fingerprint, problems)。有问题的响应不写缓存，旧缓存有问题则重新请求。"""
    fingerprint = json_fingerprint({"state": state, "questions": questions, "model": client.config["model"]})
    path = cache_path(out_dir, fingerprint)
    if path.exists():
        try:
            with path.open(encoding="utf-8") as handle:
                response = json.load(handle)
            validate_answer_response(response, questions.keys())
            problems = answer_problems(response["answers"], questions)
            if not problems and response["model"] == client.config["model"]:
                return response, {"attempts": 0, "latency_ms": 0, "cache_hit": True}, fingerprint, problems
        except (OSError, ValueError, TypeError):
            pass  # A damaged cache is disposable; input files are not.
    response, transport = client.evaluate(state, questions)
    validate_answer_response(response, questions.keys())
    problems = answer_problems(response["answers"], questions)
    if response["model"] != client.config["model"]:
        problems.append("response_model_mismatch")
    if not problems:
        write_json(response, path)
    transport = {**transport, "cache_hit": False}
    return response, transport, fingerprint, problems


def decision_from(answers, problems, min_confidence=None):
    """由代码决定复核路由与优先级。

    所有候选都进入人工复核；flags 说明需要人注意的原因。priority =
    可用层（非 exclude / insufficient）的概率之和 × fit 期望 ÷ 3，答案有问题时为 0。
    min_confidence 为 None 时只记录 confidence、不据此标记，等冻结样本校准后再设。
    """
    flags = list(problems)
    answers = {key: value if isinstance(value, dict) else {} for key, value in answers.items()}
    layer = answers.get("layer") if isinstance(answers.get("layer"), dict) else {}
    if layer.get("choice") in {"cross_domain", "canonical"}:
        flags.append("layer_requires_independent_evidence")
    if layer.get("choice") in {"exclude", "insufficient"}:
        flags.append(f"layer={layer.get('choice')}")
    confidence = layer.get("confidence")
    if min_confidence is not None and (not isinstance(confidence, (int, float)) or confidence < min_confidence):
        flags.append("low_confidence")
    conflict = (answers.get("negative_conflict") or {}).get("noul")
    if isinstance(conflict, (int, float)) and conflict >= 0.5:
        flags.append("negative_axis_conflict")
    if (answers.get("evidence_readiness") or {}).get("choice") != "abstract_enough":
        flags.append("evidence_not_abstract_level")
    if (answers.get("support_sentence") or {}).get("choice") == "none":
        flags.append("no_support_sentence")
    probabilities = layer.get("probabilities") if isinstance(layer.get("probabilities"), dict) else {}
    usable = sum(p for k, p in probabilities.items() if k not in {"exclude", "insufficient"} and isinstance(p, (int, float)))
    fit = (answers.get("fit") or {}).get("score")
    fit = fit if isinstance(fit, (int, float)) and not isinstance(fit, bool) else 0
    priority = 0.0 if problems else round(min(1.0, usable / (sum(probabilities.values()) or 1)) * fit / 3, 4)
    return {"route": "human_review", "flags": flags, "priority": priority, "layer_confidence": confidence}


def _rank_run(config, profile, records, skipped, status, error=None):
    run = {
        "schema_version": RANK_RUN_SCHEMA,
        "run_id": str(uuid.uuid4()),
        "created_at": utc_now(),
        "status": status,
        "configuration": {"model_requested": config["model"], "mode": config["mode"],
                          "external_send_scope": config["scope"], "min_confidence": config.get("min_confidence"),
                          "remote_card_fields": list(REMOTE_CARD_FIELDS)},
        "question_set_version": QUESTION_SET_VERSION,
        "profile": profile,
        "records": records,
        "skipped": skipped,
        "selection_policy": "shadow keeps input order; review orders by code-computed priority; every record needs human review",
    }
    if error is not None:
        run["error"] = error
    return run


def preflight(profile, candidates, out_dir, limit=None, seen=None):
    from fetch import identity_keys, load_list, seed_identity
    validate_profile(profile)
    if not isinstance(candidates, list):
        raise ValueError("candidates.json 必须为 JSON 数组")
    if limit is not None and limit < 1:
        raise ValueError("limit 必须为正整数")
    ids, dois = seed_identity(out_dir)
    seeds = set().union(*(identity_keys(x) for x in [*profile["seed_ids"], *ids, *dois]))
    seen = load_list(Path(out_dir) / "seen.json", "seen.json") if seen is None else seen
    used = set().union(*(identity_keys(x) for x in seen))
    eligible, skipped, deferred = [], [], []
    for index, candidate in enumerate(candidates):
        reasons = eligibility(candidate)
        keys = identity_keys(candidate) if isinstance(candidate, dict) else set()
        if keys & seeds: reasons.append("seed")
        if keys & used: reasons.append("seen_or_duplicate")
        if isinstance(candidate, dict) and (candidate.get("is_retracted") or candidate.get("metadata_conflict")):
            reasons.append("metadata_requires_review")
        if reasons:
            skipped.append({"candidate_id": candidate.get("id") if isinstance(candidate, dict) else None, "input_index": index, "reasons": reasons})
        elif limit is not None and len(eligible) >= limit:
            deferred.append(candidate.get("id"))
        else:
            eligible.append(candidate)
            used.update(keys)
    return eligible, skipped, deferred


def run_plan(profile, candidates, out_dir, model=DEFAULT_MODEL, limit=None, seen=None):
    items, skipped, deferred = preflight(profile, candidates, out_dir, limit, seen)
    requests = []
    for item in items:
        card = public_candidate_card(item)
        requests.append({"model": model, "state": {"data_scope": ALLOWED_SCOPE, "profile": remote_profile_context(profile), "candidate": remote_card(card)}, "questions": questions_for(profile, card)})
    plan = {"schema_version": "paper-radar-jev-plan-v1", "network_calls": 0, "requests": requests, "skipped": skipped, "deferred": deferred}
    path = audit_path(out_dir, "send-plan")
    write_json(plan, path)
    return plan, path


def run_rank(config, profile, candidates, out_dir, limit=None, client=None, seen=None):
    items, skipped, deferred = preflight(profile, candidates, out_dir, limit, seen)
    client = client or JevClient(config)
    records = []
    path = audit_path(out_dir, "rank-run")
    run = _rank_run(config, profile, records, skipped, "running")
    run["deferred"] = deferred
    write_json(run, path)
    current_id = None
    try:
        for candidate in items:
            current_id = candidate["id"]
            card = public_candidate_card(candidate)
            state = {"data_scope": config["scope"], "profile": remote_profile_context(profile), "candidate": remote_card(card)}
            questions = questions_for(profile, card)
            response, transport, fingerprint, problems = cached_or_evaluate(client, out_dir, state, questions)
            decision = decision_from(response["answers"], problems, config.get("min_confidence"))
            records.append({"candidate_id": card["id"], "input_fingerprint": fingerprint, "candidate_card": card,
                            "answers": response["answers"], "problems": problems, "response_model": response["model"],
                            "usage": response["usage"], "transport": transport, "decision": decision})
            write_json(run, path)  # Completed requests survive interruption of the next request.
    except BaseException as error:
        run.update(status="failed_partial", error=type(error).__name__, failed_candidate_id=current_id)
        write_json(run, path)
        raise
    if config["mode"] == "review":
        records.sort(key=lambda item: (-item["decision"]["priority"], item["candidate_id"]))
        for order, item in enumerate(records, 1):
            item["review_order"] = order
    run["status"] = "complete"
    write_json(run, path)
    return run, path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("smoke", "rank", "plan"):
        command = sub.add_parser(name)
        command.add_argument("--env-file", help="默认读取 Skill 根目录的 .env")
        command.add_argument("--timeout", type=int, default=40)
    smoke = sub.choices["smoke"]
    smoke.add_argument("--out-dir", default="bear-sift-data/_jev-smoke", help="只保存公开的脱敏 smoke 审计记录")
    for name in ("rank", "plan"):
        rank = sub.choices[name]
        rank.add_argument("--profile", required=True)
        rank.add_argument("--candidates", required=True)
        rank.add_argument("--out", required=True)
        rank.add_argument("--limit", type=int)
    args = parser.parse_args(argv)
    try:
        if args.timeout < 1 or (getattr(args, "limit", None) is not None and args.limit < 1):
            raise ConfigError("--timeout 和 --limit 须为正整数")
        if args.command == "plan":
            from fetch import load_list
            profile = read_profile(args.profile)
            candidates = load_list(args.candidates, "candidates.json")
            seen = load_list(Path(args.candidates).parent / "seen.json", "seen.json")
            _, path = run_plan(profile, candidates, args.out, limit=args.limit, seen=seen)
            print(f"仅本地生成外发预览，未调用 API：{path}")
            return 0
        config = load_config(args.env_file)
        if args.command == "smoke":
            record, path = run_smoke(config, args.out_dir, JevClient(config, timeout=args.timeout))
            answer = record["result"]["answers"]["english_abstract_status"].get("choice")
            print(f"Jev 连通成功｜请求模型 {config['model']}｜响应模型 {record['result']['model']}｜英文摘要判断 {answer}｜审计记录 {path}")
        else:
            profile = read_profile(args.profile)
            with open(args.candidates, encoding="utf-8") as handle:
                candidates = json.load(handle)
            from fetch import load_list
            seen = load_list(Path(args.candidates).parent / "seen.json", "seen.json")
            run, path = run_rank(config, profile, candidates, args.out, args.limit, JevClient(config, timeout=args.timeout), seen=seen)
            flagged = sum(1 for item in run["records"] if item["decision"]["flags"])
            print(f"Jev {config['mode']} 运行完成｜已评估 {len(run['records'])}｜带标记 {flagged}｜跳过 {len(run['skipped'])}｜审计记录 {path}")
    except ConfigError as error:  # 配置或用法问题
        print(f"USAGE: {error}", file=sys.stderr)
        return 64
    except ApiError as error:  # 上游在重试后仍失败
        print(f"UPSTREAM: {error}", file=sys.stderr)
        return 3
    except (OSError, ValueError) as error:  # 输入文件缺失或数据校验失败（含 JSON 解析错误）
        print(f"STATE: {error}", file=sys.stderr)
        return 65
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
