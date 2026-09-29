"""Bind Jev decisions to a frozen candidate pool and the final reading list."""
import hashlib
import json

import jev_rank

SCHEMA = "bear-sift-screening-v1"


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def make_packet(pool, profile, cards, *, run=None, reason=None):
    if run is None and not (isinstance(reason, str) and reason.strip()):
        raise ValueError("基础模式须记录原因")
    if run is not None and (run.get("status") != "complete" or run.get("configuration", {}).get("mode") != "review"):
        raise ValueError("主流程需要已完成的 Jev review 运行")
    return {"schema_version": SCHEMA, "mode": "jev" if run is not None else "basic",
            "pool_hash": fingerprint(pool), "profile_hash": fingerprint(profile),
            "baseline": cards, "reason": reason, "jev_run": run}


def records(packet):
    return {r["candidate_id"]: r for r in (packet.get("jev_run") or {}).get("records", [])}


def validate_packet(packet, pool, profile):
    if not isinstance(packet, dict) or packet.get("schema_version") != SCHEMA:
        return ["须提供本期 screening.json，使用 sift.py screen 生成"]
    errors = []
    if packet.get("pool_hash") != fingerprint(pool) or packet.get("profile_hash") != fingerprint(profile):
        return ["筛选记录与当前候选池或画像不一致，请重新 screen；相同 Jev 输入可命中缓存"]
    if packet.get("mode") == "basic":
        if not isinstance(packet.get("reason"), str) or not packet["reason"].strip():
            errors.append("基础模式缺少原因")
        if packet.get("jev_run") is not None:
            errors.append("基础模式不可带有 Jev 运行并冒充已筛选")
        return errors
    if packet.get("mode") != "jev":
        return ["未知筛选模式"]
    run = packet.get("jev_run")
    if not isinstance(run, dict) or run.get("status") != "complete":
        return ["Jev 运行未完整完成，请重试或显式选择基础模式"]
    if run.get("schema_version") != jev_rank.RANK_RUN_SCHEMA or run.get("question_set_version") != jev_rank.QUESTION_SET_VERSION:
        errors.append("Jev 运行或问题集版本不匹配")
    if run.get("profile") != profile or run.get("configuration", {}).get("mode") != "review":
        errors.append("Jev 画像或模式不匹配")
    items = run.get("records")
    if not isinstance(items, list):
        return errors + ["Jev records 须为数组"]
    by_id = {p["id"]: p for p in pool}
    used = set()
    model = run.get("configuration", {}).get("model_requested")
    for record in items:
        item_id = record.get("candidate_id") if isinstance(record, dict) else None
        if item_id not in by_id or item_id in used:
            errors.append("Jev 记录包含未知或重复候选")
            continue
        used.add(item_id)
        try:
            card = jev_rank.public_candidate_card(by_id[item_id])
            questions = jev_rank.questions_for(profile, card)
            state = {"data_scope": jev_rank.ALLOWED_SCOPE, "profile": jev_rank.remote_profile_context(profile), "candidate": jev_rank.remote_card(card)}
            expected = jev_rank.json_fingerprint({"state": state, "questions": questions, "model": model})
            if record.get("input_fingerprint") != expected or jev_rank.remote_card(record.get("candidate_card", {})) != jev_rank.remote_card(card):
                errors.append(f"{item_id}: Jev 输入已过期")
            answers = record.get("answers")
            if not isinstance(answers, dict):
                errors.append(f"{item_id}: Jev 答案无效")
                continue
            problems = jev_rank.answer_problems(answers, questions)
            if record.get("response_model") != model:
                problems.append("response_model_mismatch")
            decision = jev_rank.decision_from(answers, problems, run["configuration"].get("min_confidence"))
            if record.get("problems") != problems or record.get("decision") != decision:
                errors.append(f"{item_id}: Jev 校验或排序记录已变化")
        except (KeyError, TypeError, ValueError):
            errors.append(f"{item_id}: 无法验证 Jev 记录")
    return errors


def pick_issues(pick, packet):
    if packet["mode"] == "basic":
        return []
    record = records(packet).get(pick.get("id"))
    if record is None:
        return ["本篇未经 Jev 评估"]
    if record.get("problems"):
        return ["本篇 Jev 返回未通过契约校验"]
    answer = record["answers"]["layer"]["choice"]
    issues = []
    if answer != pick.get("layer"):
        issues.append(f"Jev 分层为 {answer}，最终分层为 {pick.get('layer')}")
    # The independent evidence flag is handled by canonical_basis/isomorphism.
    issues.extend(flag for flag in record["decision"]["flags"] if flag != "layer_requires_independent_evidence")
    return issues


def validate_picks(selection, packet, pool, profile):
    errors = validate_packet(packet, pool, profile)
    if errors:
        return errors
    for pick in selection.get("picks", []):
        if not isinstance(pick, dict):
            continue
        issues = pick_issues(pick, packet)
        if issues and not (isinstance(pick.get("screening_note"), str) and pick["screening_note"].strip()):
            errors.append(f"{pick.get('id')}: {'；'.join(issues)}；须填写 screening_note 说明采用依据")
    return errors


def summary(packet):
    if packet["mode"] == "basic":
        return "基础模式，未调用 Jev。原因：" + packet["reason"]
    run = packet["jev_run"]
    good = sum(not r["problems"] for r in run["records"])
    return (f"Jev {run['configuration']['model_requested']}：有效评估 {good} 篇，"
            f"异常响应 {len(run['records']) - good} 篇，跳过 {len(run.get('skipped', []))} 篇，"
            f"超出调用上限暂缓 {len(run.get('deferred', []))} 篇。宿主 Agent 复核并决定最终采用。")


def pick_evidence(pick, packet):
    if packet["mode"] == "basic":
        return []
    record = records(packet).get(pick["id"])
    result = []
    if record is None or record.get("problems"):
        result.append("Jev 状态：本篇无有效筛选结果。")
    else:
        answers = record["answers"]
        result.append(f"Jev 判断：{answers['layer']['choice']}；研究轴 {answers['match_axis']['choice']}；匹配分 {answers['fit']['score']}/3。")
        sentence_id = answers["support_sentence"]["choice"]
        sentences = {s["id"]: s["text"] for s in record["candidate_card"]["abstract_sentences"]}
        if sentence_id in sentences:
            result.append("摘要依据（作者原文）：" + sentences[sentence_id])
        if record["decision"]["flags"]:
            result.append("复核信号：" + ", ".join(record["decision"]["flags"]))
    if pick.get("screening_note"):
        result.append("采用说明：" + pick["screening_note"])
    return result


def review_markdown(packet):
    text = ["# Bear Paper Pick 筛选审阅", "", summary(packet), "",
            "按下面的顺序审阅。最终选择仅填写候选 ID、层、理由和必要的复核说明；Jev 分数不代表论文质量。", ""]
    if packet["mode"] == "jev":
        for record in packet["jev_run"]["records"]:
            card = record["candidate_card"]
            text.extend([f"## {card['id']}: {card['title']}", ""])
            text.extend(pick_evidence({"id": card["id"]}, packet))
            text.append("")
        for label in ("skipped", "deferred"):
            text.extend([f"## {label}", "", json.dumps(packet["jev_run"].get(label, []), ensure_ascii=False), ""])
    else:
        for card in packet["baseline"]:
            text.append(f"{card['id']}: {card['title']}；规则分 {card['rule_score']}")
    return "\n".join(text) + "\n"
