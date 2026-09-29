#!/usr/bin/env python3
"""Create and validate the fixed profile input for Jev semantic triage.

Usage:
  profile_snapshot.py create --line NAME --axis ID=DESCRIPTION [--axis ...]
                             [--negative-axis ID=DESCRIPTION] --out FILE
  profile_snapshot.py validate PROFILE.json

This file contains selection criteria, never API credentials or user source text.
"""
import argparse
import json
import re
import sys
from pathlib import Path

SCHEMA_VERSION = "paper-radar-profile-v1"
AXIS_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
LAYER_IDS = ("core", "adjacent", "cross_domain", "canonical")
DEFAULT_LAYER_TARGETS = {"core": "2-4", "adjacent": "2-3", "cross_domain": "0-2", "canonical": "1-2"}


def parse_axis(value):
    if "=" not in value:
        raise ValueError("画像轴须为 ID=说明，例如 method=Uses causal inference")
    axis_id, description = (part.strip() for part in value.split("=", 1))
    if not AXIS_ID.fullmatch(axis_id):
        raise ValueError(f"画像轴 ID 不合法：{axis_id}")
    if not description:
        raise ValueError(f"画像轴说明不能为空：{axis_id}")
    return {"id": axis_id, "description": description}


def parse_layer_target(value):
    if "=" not in value:
        raise ValueError("分层配比须为 layer=min-max，例如 core=2-4")
    layer, target = (part.strip() for part in value.split("=", 1))
    match = re.fullmatch(r"(\d+)-(\d+)", target)
    if layer not in LAYER_IDS or not match or int(match.group(1)) > int(match.group(2)):
        raise ValueError("分层配比须使用 core、adjacent、cross_domain、canonical 和非递减 min-max")
    return layer, target


def validate_profile(profile):
    if not isinstance(profile, dict):
        raise ValueError("profile 必须为 JSON 对象")
    required = ("schema_version", "line_name", "seed_ids", "positive_axes", "negative_axes", "logical_form", "layer_targets", "language")
    missing = [key for key in required if key not in profile]
    if missing:
        raise ValueError("profile 缺少字段：" + ", ".join(missing))
    if profile["schema_version"] != SCHEMA_VERSION:
        raise ValueError(f"不支持的 profile schema：{profile['schema_version']}")
    if not isinstance(profile["line_name"], str) or not profile["line_name"].strip():
        raise ValueError("line_name 必须为非空字符串")
    if not isinstance(profile["seed_ids"], list) or not profile["seed_ids"] or not all(isinstance(seed, str) and seed.strip() for seed in profile["seed_ids"]):
        raise ValueError("seed_ids 必须包含至少一个已确认种子 ID")
    if not isinstance(profile["logical_form"], str) or not profile["logical_form"].strip():
        raise ValueError("logical_form 必须为非空字符串")
    if profile["language"] != "en":
        raise ValueError("首发只支持 language=en 的英文摘要筛选")
    if not isinstance(profile["layer_targets"], dict) or set(profile["layer_targets"]) != set(LAYER_IDS):
        raise ValueError("layer_targets 必须完整包含四层")
    for layer, target in profile["layer_targets"].items():
        parse_layer_target(f"{layer}={target}")
    if not isinstance(profile["positive_axes"], list) or not profile["positive_axes"]:
        raise ValueError("positive_axes 至少需要一条明确轴")
    seen = set()
    for key in ("positive_axes", "negative_axes"):
        axes = profile[key]
        if not isinstance(axes, list):
            raise ValueError(f"{key} 必须为数组")
        for axis in axes:
            if not isinstance(axis, dict) or set(axis) != {"id", "description"}:
                raise ValueError(f"{key} 中每条轴只允许 id 和 description")
            if not isinstance(axis["id"], str) or not AXIS_ID.fullmatch(axis["id"]):
                raise ValueError(f"{key} 存在不合法 id")
            if not isinstance(axis["description"], str) or not axis["description"].strip():
                raise ValueError(f"{key} 存在空说明")
            if axis["id"] in seen:
                raise ValueError(f"画像轴 ID 重复：{axis['id']}")
            if axis["id"] in {"none", "no_negative_axis"}:
                raise ValueError("画像轴 ID 使用了保留选项")
            seen.add(axis["id"])
    return profile


def read_profile(path):
    with open(path, encoding="utf-8") as handle:
        return validate_profile(json.load(handle))


def write_json(value, path):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    from fetch import write_json as atomic_write
    atomic_write(value, target)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create", help="创建可复放的画像快照")
    create.add_argument("--line", required=True, help="兴趣线名称")
    create.add_argument("--seed", action="append", required=True, help="已确认的 OpenAlex 或 DOI 种子 ID")
    create.add_argument("--axis", action="append", required=True, help="正画像轴：ID=英文说明")
    create.add_argument("--negative-axis", action="append", default=[], help="负画像轴：ID=英文说明")
    create.add_argument("--logical-form", default="", help="可选的英文逻辑形说明")
    create.add_argument("--layer-target", action="append", default=[], help="可选配比：core=2-4")
    create.add_argument("--out", required=True)
    validate = sub.add_parser("validate", help="校验已有画像快照")
    validate.add_argument("profile")
    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            layer_targets = dict(DEFAULT_LAYER_TARGETS)
            for item in args.layer_target:
                layer, target = parse_layer_target(item)
                layer_targets[layer] = target
            profile = {
                "schema_version": SCHEMA_VERSION,
                "line_name": args.line.strip(),
                "seed_ids": [seed.strip() for seed in args.seed],
                "positive_axes": [parse_axis(item) for item in args.axis],
                "negative_axes": [parse_axis(item) for item in args.negative_axis],
                "logical_form": args.logical_form.strip(),
                "layer_targets": layer_targets,
                "language": "en",
            }
            validate_profile(profile)
            write_json(profile, args.out)
            print(f"画像快照已写入：{args.out}")
        else:
            profile = read_profile(args.profile)
            print(f"有效画像：{profile['line_name']}｜正画像轴 {len(profile['positive_axes'])}｜负画像轴 {len(profile['negative_axes'])}")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
