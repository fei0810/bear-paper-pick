#!/usr/bin/env python3
"""Deterministic selection validation, rendering and registration.

The host agent proposes selection.json. This CLI owns bibliographic rendering
and mechanical acceptance, while seen.json remains the sole pushed-state source.
"""
import argparse
from collections import Counter
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import sys

import fetch
import jev_rank
import screening
from profile_snapshot import LAYER_IDS, read_profile

LAYERS = dict(zip(LAYER_IDS, ("核心近邻", "相邻启发", "跨域同构", "经典回溯")))


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def context(pool_path, profile_path):
    pool_path = Path(pool_path)
    profile = read_profile(profile_path)
    pool = fetch.load_list(pool_path, "candidates.json")
    if any(not isinstance(p, dict) or not p.get("id") or not p.get("title") for p in pool):
        raise ValueError("候选记录须有 id 和 title")
    if len({p["id"] for p in pool}) != len(pool):
        raise ValueError("候选池有重复 id，须先修复")
    ids, dois = fetch.seed_identity(pool_path.parent)
    seeds = set().union(*(fetch.identity_keys(x) for x in [*ids, *dois, *profile["seed_ids"]]))
    seen = fetch.load_list(pool_path.parent / "seen.json", "seen.json")
    return pool, profile, seeds, seen


def validate(selection, pool, profile, seeds, seen, packet=None):
    if not isinstance(selection, dict) or not isinstance(selection.get("picks"), list):
        return ["selection 须为包含 picks 数组的对象"]
    errors, counts, cited = [], Counter(), Counter()
    if selection.get("line") != profile["line_name"]:
        errors.append("selection.line 与画像不一致")
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,79}", str(selection.get("run_id", ""))):
        errors.append("run_id 须为安全的文件名，至多 80 字符")
    try:
        cutoff = dt.date.fromisoformat(selection["as_of"])
        if cutoff > dt.date.today():
            errors.append("as_of 不可晚于今天")
    except (KeyError, TypeError, ValueError):
        cutoff = dt.date.today()
        errors.append("as_of 须为 YYYY-MM-DD")
    policy = profile.get("selection_policy", {})
    window = policy.get("recent_since")
    if window:
        dt.date.fromisoformat(window)
    maximum = policy.get("max_per_seed", 2)
    if not isinstance(maximum, int) or isinstance(maximum, bool) or maximum < 1:
        raise ValueError("selection_policy.max_per_seed 须为正整数")
    by_id = {p["id"]: p for p in pool}
    pushed = set().union(*(fetch.identity_keys(x) for x in seen))
    used = set()
    for pick in selection["picks"]:
        if not isinstance(pick, dict):
            errors.append("每项选题须为对象")
            continue
        paper_id, layer = pick.get("id"), pick.get("layer")
        item = by_id.get(paper_id)
        if not item:
            errors.append(f"{paper_id}: 不在候选池")
            continue
        keys = fetch.identity_keys(item)
        if keys & seeds: errors.append(f"{paper_id}: 是种子或已登记版本")
        if keys & pushed: errors.append(f"{paper_id}: 已推送")
        if keys & used: errors.append(f"{paper_id}: 本期重复论文或 DOI")
        used.update(keys)
        if layer not in LAYERS:
            errors.append(f"{paper_id}: 无效分层")
        else:
            counts[layer] += 1
        if not isinstance(pick.get("why"), str) or not pick["why"].strip():
            errors.append(f"{paper_id}: 缺少推荐理由")
        if layer == "cross_domain" and not str(pick.get("isomorphism") or "").strip():
            errors.append(f"{paper_id}: 缺少跨域同构说明")
        if layer == "canonical" and not str(pick.get("canonical_basis") or "").strip():
            errors.append(f"{paper_id}: 经典层须有可复核的引用或历史依据")
        if item.get("is_retracted") or item.get("metadata_conflict"):
            errors.append(f"{paper_id}: 元数据风险未解决")
        publication = item.get("publication_date")
        if window and layer in {"core", "adjacent"} and item.get("date_precision") == "unknown":
            errors.append(f"{paper_id}: 核验未获得完整日期，无法确认本期资格")
        if publication:
            try:
                date = dt.date.fromisoformat(publication)
                if date > cutoff: errors.append(f"{paper_id}: 发表日期晚于截止日")
                if layer in {"core", "adjacent"} and window and publication < window:
                    errors.append(f"{paper_id}: 不在本期时间窗")
            except ValueError:
                errors.append(f"{paper_id}: 无效发表日期")
        elif window and layer in {"core", "adjacent"}:
            errors.append(f"{paper_id}: 缺完整日期，无法确认本期资格")
        elif isinstance(item.get("year"), int) and item["year"] > cutoff.year:
            errors.append(f"{paper_id}: 年份晚于截止年")
        for seed in set(item.get("cites_seeds") or filter(None, [item.get("cites_seed")])):
            cited[seed] += 1
    for seed, count in cited.items():
        if count > maximum: errors.append(f"{seed}: 同种子施引选了 {count} 篇，上限 {maximum}")
    shortfalls = selection.get("shortfalls", {})
    if not isinstance(shortfalls, dict):
        errors.append("shortfalls 须为对象")
        shortfalls = {}
    for layer, target in profile["layer_targets"].items():
        lower, upper = map(int, target.split("-"))
        if counts[layer] > upper:
            errors.append(f"{layer}: 超过配额 {upper}")
        if counts[layer] < lower and not str(shortfalls.get(layer) or "").strip():
            errors.append(f"{layer}: 不足 {lower} 篇，须说明缺额原因，禁止填充无关论文")
    if packet is not None:
        errors.extend(screening.validate_picks(selection, packet, pool, profile))
    return errors


def channel_summary(reports, skips):
    result = {layer: [] for layer in LAYERS}
    for argument in reports:
        layer, path = argument.split("=", 1)
        if layer not in result: raise ValueError("未知通道层")
        report = read(path)
        if report.get("schema_version") != "paper-radar-fetch-run-v1":
            raise ValueError("通道战报须来自 fetch.py --report")
        result[layer].append({"channel": report["channel"], "status": report["status"], "returned": report["returned"], "run_id": report["run_id"], "source_hash": fingerprint(report), "requests": report.get("requests", []), "limits": report.get("limits", [])})
    for argument in skips:
        layer, reason = argument.split("=", 1)
        if layer not in result or not reason.strip(): raise ValueError("跳过须指定层和理由")
        result[layer].append({"status": "skipped", "reason": reason})
    missing = [k for k, v in result.items() if not v]
    if missing: raise ValueError("通道战报缺层：" + ", ".join(missing))
    return result


def md(value):
    return re.sub(r"([\\`*_{}\[\]<>#|])", r"\\\1", str(value or "")).replace("\n", " ")


def render(selection, pool, profile, seeds, seen, channels, destination, packet=None):
    if packet is None:
        raise ValueError("本期交付需要 screening.json；基础模式也须明确记录原因")
    errors = validate(selection, pool, profile, seeds, seen, packet)
    if errors: raise ValueError("；".join(errors))
    destination = Path(destination)
    if destination.exists(): raise ValueError("输出目录已存在，请指定新的期次目录")
    by_id = {p["id"]: p for p in pool}
    chosen = [by_id[p["id"]] for p in selection["picks"]]
    text = [f"# Bear Paper Pick：{md(selection['line'])}", "", f"截至 {selection['as_of']}，近邻与相邻窗口自 {profile.get('selection_policy', {}).get('recent_since') or '未指定'}。推荐理由由宿主 Agent 撰写，书目字段从冻结候选记录生成；摘要只支持相关性判断。", "", "## 通道战报", ""]
    text[4:4] = ["## 筛选方式", "", md(screening.summary(packet)), ""]
    for layer, entries in channels.items():
        parts = []
        for entry in entries:
            if entry["status"] == "skipped": parts.append("跳过：" + md(entry["reason"]))
            else:
                detail = f"{entry['channel']} 取回 {entry['returned']} 条"
                for limit in entry.get("limits", []):
                    if limit.get("truncated"): detail += f"（命中 {limit['total']}，达到本次上限，结果有截断）"
                    if "reference_union" in limit: detail += f"（参考并集 {limit['reference_union']}，解析 {limit['resolved']}；按共引或前 20 入池）"
                    if "sampling" in limit: detail += "（" + limit["sampling"] + "）"
                parts.append(detail)
        text.append(f"{LAYERS[layer]}：{'；'.join(parts)}。")
        text.append("")
    text.extend(["各通道数量允许重叠；取回数不等于去重候选数或推荐数。", ""])
    number = 0
    for layer, label in LAYERS.items():
        text.extend([f"## {label}", ""])
        picks = [p for p in selection["picks"] if p["layer"] == layer]
        for pick in picks:
            number += 1
            item = by_id[pick["id"]]
            authors = item.get("authors") or []
            author_text = "；".join(authors) if len(authors) <= 3 else authors[0] + " 等；末位作者 " + authors[-1]
            date = item.get("publication_date") or (str(item.get("year")) + "（仅年份）" if item.get("year") else "日期缺失")
            date += "（Crossref 完整日期）" if item.get("metadata_verification", {}).get("publication_date") else "（来源记录，未独立核验）"
            text.extend([f"### {number}. {md(item['title'])}", "", f"{md(author_text or '作者缺失')}。{md(item.get('venue') or '刊名缺失')}。{date}。", ""])
            doi = fetch.doi_key(item.get("doi"))
            link = "https://doi.org/" + doi if doi else ("https://openalex.org/" + item['id'] if re.fullmatch(r"W\d+", item['id']) else None)
            if link: text.extend([f"[来源]({link})", ""])
            text.extend(["推荐理由：" + md(pick["why"]), ""])
            for evidence in screening.pick_evidence(pick, packet):
                text.extend([md(evidence), ""])
            if layer == "cross_domain": text.extend(["同构与迁移边界：" + md(pick["isomorphism"]), ""])
            if layer == "canonical": text.extend(["经典依据：" + md(pick["canonical_basis"]), ""])
            text.extend([f"候选 ID：{md(item['id'])}。取数时间：{md(item.get('fetched_at') or '旧记录未保存')}。", ""])
        if not picks: text.extend([md(selection.get("shortfalls", {}).get(layer) or "本期无满足条件的候选。"), ""])
        elif layer in selection.get("shortfalls", {}): text.extend(["配额说明：" + md(selection["shortfalls"][layer]), ""])
    text.extend(["## 反馈", "", "可回复条目编号加 👍 或 👎，也可说明原因；未说明原因时只记录反馈，不推断偏好。", ""])
    keys = set()
    bib = "\n\n".join(fetch.bibtex_record(item, used_keys=keys) for item in chosen) + "\n"
    destination.mkdir(parents=True)
    (destination / "digest.md").write_text("\n".join(text), encoding="utf-8")
    (destination / "references.bib").write_text(bib, encoding="utf-8")
    fetch.write_json(selection, destination / "selection.json")
    fetch.write_json(packet, destination / "screening.json")
    receipt = {"schema_version": "bear-sift-delivery-v1", "created_at": fetch.utc_now(), "selection": selection,
               "selected_metadata": chosen, "pool_hash": fingerprint(pool), "profile_hash": fingerprint(profile), "channels": channels,
               "files": {name: hashlib.sha256((destination / name).read_bytes()).hexdigest() for name in ("digest.md", "references.bib", "selection.json", "screening.json")}}
    fetch.write_json(receipt, destination / "receipt.json")
    return receipt


def register(receipt_path, pool_path, profile_path):
    receipt_path = Path(receipt_path)
    receipt = read(receipt_path)
    if receipt.get("schema_version") not in {"paper-radar-delivery-v1", "bear-sift-delivery-v1"}: raise ValueError("交付回执类型错误")
    if receipt["schema_version"] == "bear-sift-delivery-v1" and set(receipt["files"]) != {"digest.md", "references.bib", "selection.json", "screening.json"}:
        raise ValueError("新回执缺少筛选证据或交付文件")
    for name, digest in receipt["files"].items():
        if name not in {"digest.md", "references.bib", "selection.json", "screening.json"}: raise ValueError("非法交付文件名")
        if hashlib.sha256((receipt_path.parent / name).read_bytes()).hexdigest() != digest:
            raise ValueError("交付文件已改动，请重新校验和渲染")
    selection = receipt["selection"]
    if "selection.json" in receipt["files"] and read(receipt_path.parent / "selection.json") != selection:
        raise ValueError("回执与选题文件内容不一致")
    token = fingerprint(selection)
    seen_path = Path(pool_path).parent / "seen.json"
    with fetch.locked(seen_path):
        pool, profile, seeds, seen = context(pool_path, profile_path)
        if fingerprint(pool) != receipt["pool_hash"] or fingerprint(profile) != receipt["profile_hash"]:
            raise ValueError("候选池或画像已变化，须重新渲染")
        previous = [x for x in seen if x.get("delivery") != token]
        packet = read(receipt_path.parent / "screening.json") if "screening.json" in receipt["files"] else None
        errors = validate(selection, pool, profile, seeds, previous, packet)
        if errors: raise ValueError("；".join(errors))
        existing = {x["id"] for x in seen}
        by_id = {p["id"]: p for p in pool}
        for pick in selection["picks"]:
            item = by_id[pick["id"]]
            if item["id"] not in existing:
                seen.append({"id": item["id"], "doi": item.get("doi"), "aliases": item.get("aliases", []), "title": item["title"], "date": dt.date.today().isoformat(), "feedback": [], "delivery": token})
        fetch.write_json(seen, seen_path)
    return len(selection["picks"])


def prerank(pool, profile, seeds, seen, top=40):
    """Transparent lexical/channel baseline; no claim of scientific relevance."""
    stop = {"with", "from", "that", "this", "using", "uses", "research", "methods", "method", "data", "models", "model", "only", "into", "their", "through"}
    words = set(re.findall(r"[a-z]{4,}", " ".join(x["description"] for x in profile["positive_axes"]).lower())) - stop
    used = seeds | set().union(*(fetch.identity_keys(x) for x in seen))
    cards = []
    for item in pool:
        if fetch.identity_keys(item) & used: continue
        channels = item.get("channels") or [item.get("channel")]
        matches = sorted(words & set(re.findall(r"[a-z]{4,}", (item["title"] + " " + item.get("abstract", "")).lower())))
        cites = item.get("cites_seeds") or list(filter(None, [item.get("cites_seed")]))
        score = len(matches) + min(len(channels), 3) + 2 * bool(cites)
        cards.append({"id": item["id"], "title": item["title"], "year": item.get("year"), "publication_date": item.get("publication_date"), "channels": channels, "rule_score": score, "matched_words": matches, "abstract_available": bool(item.get("abstract"))})
    return sorted(cards, key=lambda x: (-x["rule_score"], x["id"]))[:top]


def screen(pool_path, profile_path, destination, mode="jev", reason=None, limit=40, plan_only=False, env_file=None, client=None):
    if limit < 1 or limit > 200:
        raise ValueError("每次 screen 的 limit 须在 1 至 200 之间")
    pool, profile, seeds, seen = context(pool_path, profile_path)
    baseline = prerank(pool, profile, seeds, seen, max(1, len(pool)))
    by_id = {p["id"]: p for p in pool}
    ordered = [by_id[c["id"]] for c in baseline]
    destination = Path(destination)
    if (destination / "screening.json").exists() or (destination / "review.md").exists():
        raise ValueError("筛选输出已存在，请使用新的期次或修订目录")
    if plan_only:
        if mode != "jev": raise ValueError("基础模式无需外发预览")
        selected_env = jev_rank.resolve_env_file(env_file)
        values = jev_rank.read_dotenv(selected_env)
        model = jev_rank.os.environ.get("JEV_MODEL") or values.get("JEV_MODEL") or jev_rank.DEFAULT_MODEL
        return jev_rank.run_plan(profile, ordered, destination, model=model, limit=limit, seen=seen)[1]
    if mode == "basic":
        packet = screening.make_packet(pool, profile, baseline[:limit], reason=reason)
    elif mode == "jev":
        config = jev_rank.load_config(env_file)
        # The product workflow always uses review. Direct jev_rank keeps shadow for comparisons.
        config["mode"] = "review"
        run, _ = jev_rank.run_rank(config, profile, ordered, Path(pool_path).parent, limit=limit, client=client, seen=seen)
        packet = screening.make_packet(pool, profile, baseline[:limit], run=run)
    else:
        raise ValueError("未知筛选模式")
    errors = screening.validate_packet(packet, pool, profile)
    if errors: raise ValueError("；".join(errors))
    destination.mkdir(parents=True, exist_ok=True)
    fetch.write_json(packet, destination / "screening.json")
    (destination / "review.md").write_text(screening.review_markdown(packet), encoding="utf-8")
    return destination / "screening.json"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("prerank", "screen", "validate", "render", "register"):
        command = sub.add_parser(name)
        command.add_argument("--pool", required=True)
        command.add_argument("--profile", required=True)
        if name in {"validate", "render"}: command.add_argument("--selection", required=True)
        if name in {"render", "prerank", "screen"}: command.add_argument("--out", required=True)
        if name in {"validate", "render"}: command.add_argument("--screening", required=True)
        if name == "screen":
            command.add_argument("--mode", choices=("jev", "basic"), default="jev")
            command.add_argument("--reason")
            command.add_argument("--limit", type=int, default=40)
            command.add_argument("--plan-only", action="store_true")
            command.add_argument("--env-file")
        if name == "prerank": command.add_argument("--top", type=int, default=40)
        if name == "register": command.add_argument("--receipt", required=True)
        if name == "render":
            command.add_argument("--channel-report", action="append", default=[])
            command.add_argument("--channel-skip", action="append", default=[])
    args = parser.parse_args(argv)
    try:
        pool, profile, seeds, seen = context(args.pool, args.profile)
        if args.command == "screen":
            path = screen(args.pool, args.profile, args.out, args.mode, args.reason, args.limit, args.plan_only, args.env_file)
            print(f"已生成 {path}")
        elif args.command == "prerank":
            if args.top < 1: raise ValueError("--top 须为正整数")
            fetch.write_json({"baseline_version": "lexical-channels-v1", "pool_hash": fingerprint(pool), "profile_hash": fingerprint(profile), "cards": prerank(pool, profile, seeds, seen, args.top)}, args.out)
        elif args.command == "register":
            print(f"已登记 {register(args.receipt, args.pool, args.profile)} 篇（重复运行幂等）")
        else:
            selection = read(args.selection)
            packet = read(args.screening)
            errors = validate(selection, pool, profile, seeds, seen, packet)
            if errors: raise ValueError("；".join(errors))
            if args.command == "render":
                channels = channel_summary(args.channel_report, args.channel_skip)
                render(selection, pool, profile, seeds, seen, channels, args.out, packet)
                print(f"已生成 digest.md、references.bib 和 receipt.json：{args.out}")
            else: print("选题校验通过")
    except jev_rank.ApiError as error:
        print(f"UPSTREAM: {error}", file=sys.stderr)
        return 3
    except jev_rank.ConfigError as error:
        print(f"CONFIG: {error}", file=sys.stderr)
        return 64
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"STATE: {error}", file=sys.stderr)
        return 65
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
