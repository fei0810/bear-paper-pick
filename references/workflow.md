# 运行与交付

以下命令在 Skill 根目录执行，`LINE` 指向独立研究线。第一期可以是较长时间窗的冷启动阅读清单；weekly 应把画像中的 `selection_policy.recent_since` 更新为实际本期起点，不能把旧存货称为新发表。

## 画像与取数

```bash
LINE=../bear-paper-pick-data/demo
mkdir -p "$LINE"
python3 scripts/fetch.py work W4392168151
# 核对真实来源后，将种子的 W 号与 DOI 写入 "$LINE/seeds.txt"。
python3 scripts/profile_snapshot.py create --line demo \
  --seed W4392168151 --axis evaluation="Evaluate single-cell foundation models against simple baselines." \
  --logical-form "Test whether learned representations generalize under perturbations." \
  --out "$LINE/profile_snapshot.json"
python3 scripts/fetch.py seen init "$LINE/seen.json"
python3 scripts/fetch.py cites W4392168151 --since 2026-07-01 --to 2026-09-29 --limit 50 \
  --save "$LINE/candidates.json" --raw-dir "$LINE/runs/raw" --report "$LINE/runs/core.json"
python3 scripts/fetch.py search '"single-cell" AND "foundation model"' --since 2026-07-01 --to 2026-09-29 --limit 50 \
  --save "$LINE/candidates.json" --raw-dir "$LINE/runs/raw" --report "$LINE/runs/adjacent.json"
python3 scripts/sift.py prerank --pool "$LINE/candidates.json" --profile "$LINE/profile_snapshot.json" --out "$LINE/shortlist.json"
```

日期只是可运行示例，每期使用实际窗口。跨域检索词来自画像的逻辑形，不硬加 LLM 等与领域无关的限定词。refs 最多解析 100 篇并按共引规则入池；canon 目前每个种子最多采样 100 篇施引文献，属于采样正典线索，不承诺完整引文网络。

`--save` 默认只输出汇总，`--verbose` 才显示全部候选。search 按 OpenAlex 相关度取回，cites 和 authorworks 按发表日期倒序分页；超过 `--limit` 会明确报告截断。检索默认搜索标题、摘要和 OpenAlex 可用的全文字段，因此同词噪声仍需要语义筛选。原始响应含公开来源信息和本地检索词，留在数据目录，不进入 Jev payload 或发布包。

## Jev 主流程

先读取 [Jev 策略](jev-policy.md)。设定本期输出目录；plan 只写本地预览，不要求 key，也不调用网络。下面两个命令使用相同候选、画像和上限。首用前说明研究方向会外发；已有同范围授权可继续运行。

```bash
REVIEW="$LINE/review/2026-W40-r1"
python3 scripts/sift.py screen --pool "$LINE/candidates.json" --profile "$LINE/profile_snapshot.json" --out "$REVIEW" --limit 40 --plan-only
python3 scripts/sift.py screen --pool "$LINE/candidates.json" --profile "$LINE/profile_snapshot.json" --out "$REVIEW" --limit 40
```

读取 `review.md`：Jev 决定默认审阅顺序，提供分层、命中研究轴、摘要句和复核信号。`screening.json` 保存完整本地审计并绑定候选与画像。宿主 Agent 按结果选题，并对改变分层、排除项、异常回答或未经评估的采用填写具体 `screening_note`；不得用“人工确认”这种空话代替证据。

screen 固定使用 review，即使旧 `.env` 仍为 shadow；直接调用 jev_rank.py 的对照实验可保留 shadow。缺 key 或 API 失败会报错，不会自动变成基础模式。若用户选择基础模式，用独立输出目录执行：

```bash
python3 scripts/sift.py screen --pool "$LINE/candidates.json" --profile "$LINE/profile_snapshot.json" --out "$LINE/review/2026-W40-basic" --mode basic --reason "用户本期明确选择不使用远程服务"
```

以上原因仅是示例，必须改为真实原因。后续 `--screening` 指向实际使用的目录。screen 输出已经存在时使用新的修订目录，不覆盖；缓存和逐条 Jev 审计保存在研究线目录中，重试按输入命中缓存。候选没有合格英文摘要时不会发出请求，记录会显示评估数量为零。

## 选题文件

`profile_snapshot.json` 的 `layer_targets` 控制每层配额。可增加 `selection_policy`，例如 `{"recent_since":"2026-07-01","max_per_seed":2}`。recent_since 只约束近邻与相邻；跨域和经典允许跨时间回溯。已发表日期必须不晚于本期 as_of。旧数据只有年份时会明确显示“仅年份”；需要确认本期资格时，缺完整日期会被拒绝。

```json
{
  "line": "demo",
  "run_id": "2026-W40",
  "as_of": "2026-09-29",
  "picks": [
    {"id": "W4412929241", "layer": "core", "why": "直接比较单细胞扰动预测模型与简单基线。"}
  ],
  "shortfalls": {
    "core": "本期只有一篇完成来源核验。",
    "adjacent": "尚无足够证据支持的相邻候选。",
    "canonical": "本期没有新增经典条目。"
  }
}
```

示例 ID 必须已经存在于当前候选池，不能为通过校验而手写书目。跨域项另加非空 `isomorphism`，经典项另加非空 `canonical_basis`，内容由 Agent 根据实际证据填写。需要分歧采用说明时，在对应 pick 中加入 `"screening_note":"基于实际证据说明为何改变 Jev 判断或采用未评估项"`。这些自由文本的科学质量仍由研究者审核，代码仅检查是否存在和机械约束。

## 书目核验、校验、渲染、登记

```bash
# 先核对拟选条目的 DOI、题名和完整日期，保留 OpenAlex 原字段。
python3 scripts/fetch.py verify "$LINE/candidates.json" W4412929241 --raw-dir "$LINE/runs/verification"
# verify 若改变候选字段，使用新 REVIEW 目录重新 screen，更新绑定。
python3 scripts/sift.py validate --screening "$REVIEW/screening.json" --pool "$LINE/candidates.json" --profile "$LINE/profile_snapshot.json" --selection "$LINE/selection.json"
python3 scripts/sift.py render --screening "$REVIEW/screening.json" --pool "$LINE/candidates.json" --profile "$LINE/profile_snapshot.json" \
  --selection "$LINE/selection.json" --out "$LINE/digest/2026-W40" \
  --channel-report core="$LINE/runs/core.json" --channel-report adjacent="$LINE/runs/adjacent.json" \
  --channel-skip 'cross_domain=本期未找到可核验同构候选' --channel-skip 'canonical=本期未刷新经典通道'
# 检查 digest.md、references.bib 和来源链接并交付后登记。
python3 scripts/sift.py register --pool "$LINE/candidates.json" --profile "$LINE/profile_snapshot.json" --receipt "$LINE/digest/2026-W40/receipt.json"
```

每层可以传多个真实 fetch 报告；未运行或失败的通道用 `--channel-skip 层=真实原因`。战报展示通道取回量及截断信息，不把相加结果称为去重量。render 拒绝覆盖已有目录；需要修改时使用新的期次修订目录。回执绑定候选池、画像、screening.json 和交付文件哈希，之后发生变化应重新渲染，不能拿旧回执登记新内容。register 从池里取标题，不接受模型重新输入标题。

seen 是推送状态的唯一来源，候选池可以保留已推条目作为历史。prerank、Jev preflight 和最终 validate 都重新剔除已推，避免同时维护两个不同步的 status 字段。profile_snapshot 的 seed_ids 与 seeds.txt 取并集剔除：seed_ids 是画像的种子引用，seeds.txt 是经过人工确认的跨源版本注册表。

## 安全停止

出现上游错误时保留原始日志并报告通道失败；不要把无结果与失败混成同一状态。不能从摘要推断精确实验事实，不能以同 DOI 之外的相似标题自动合并版本。S2 无 key 时明确跳过。Jev 缺 key 或失败时修复重试，或者按用户选择使用显式基础模式，不把未评估写成通过。

verify 使用 Crossref 按 DOI 核验。仅有月份的刊期不会被补成每月第一天；存在完整在线日期时优先采用并记录来源，原日期保留。题名或 DOI 冲突会隔离条目，Jev 与最终选题均拒收。Crossref 无记录不等于论文不存在，可另查 DataCite、出版方或预印本页；不得把失败写成已核验。
