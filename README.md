# Bear Sift

用 Jev 沿着你的研究方向筛选论文，持续形成值得阅读的四层清单。

Bear Sift 是一个面向研究者的 Agent Skill。给出少量种子论文和研究目标，它组织公开文献检索，用 Jev 判断研究方向匹配、相邻启发与跨域迁移线索，再由宿主 Agent 结合证据完成选题。输出包括阅读清单、BibTeX、Jev 摘要依据、采用说明和可追溯的已推记录。

当前版本为 **1.9.0.dev1**，处于本地开发阶段，尚未发布到 GitHub。由 Paper Radar 1.8.0 演进，更名用于区别现有 bear 研究技能。Claude Code 和其他 Agent 的新用户验收留待下一阶段执行。

## Jev 怎样参与推荐

主流程固定采用 Jev review：从研究画像的正向轴、排除轴和逻辑形出发，对有英文摘要的候选做结构化判断；将判断、对应摘要句和复核信号组成 `review.md`。宿主 Agent 据此写选题，改变 Jev 分层、采用排除项或未完成评估的论文时，必须记录 `screening_note`。最终阅读清单展示这些依据，交付回执绑定对应筛选记录，避免换了画像仍沿用旧结果。

四层输出分别是核心近邻、相邻启发、跨域同构和经典回溯。Jev 提供的匹配分不是论文质量分；经典须补充历史或引用依据，跨域须说明迁移边界。当前没有独立研究者盲评证明 Jev 优于基础筛选。

缺少服务配置时主流程明确报错。用户可以明确选择基础模式，并留下原因；该模式不调用 Jev，在产出中标明。`shadow` 只用于独立对照，不是主工作流的默认模式。

## 安装与第一次使用

运行代码只依赖 Python 标准库。开发目标为 Python 3.12 及以上；继承版本曾在 macOS 的 3.12 与 3.14 测试，新版本本轮验证记录见 [开发状态](docs/DEVELOPMENT.md)。Windows 当前要求串行写状态，尚未正式验收。不同 Agent 的安装方式与触发行为须按 [新用户验收](docs/ACCEPTANCE.md) 实测。

本地开发时可直接让 Agent 阅读本仓库的 [SKILL.md](SKILL.md)。若宿主支持本地 Skill 目录，可将本目录安装为 `bear-sift`；运行包保留该目录名。Claude Code 常用 `~/.claude/skills/bear-sift/`，Codex 常用 `~/.codex/skills/bear-sift/`。不要把研究数据放入安装目录。

复制 `.env.example` 为 `.env`，自行填写 `TYPESAFE_API_KEY`。也可设置 `BEAR_SIFT_ENV_FILE` 指向仓库外的配置文件，所有脚本共用它；进程环境变量优先于文件。示例里没有可用密钥。[TypeSafe API 文档](https://docs.typesafe.ai/api)提供服务入口；API 服务费用由使用者承担，本项目不包含额度。

对 Agent 说：

> 用 Bear Sift，以我提供的两篇种子建立研究线，关注我指定的问题和方法，启用 Jev，生成第一期四层阅读清单。数据放在我指定的项目目录；候选不足时说明缺额。

或继续已有研究线：

> 用 Bear Sift 更新这条研究线，先读当前画像和已推记录，生成本期清单，说明 Jev 判断与最终选择的差异。

工作流要求首次外发前展示输入范围：公开题名、DOI、年份、刊名、英文摘要，以及研究画像中的方向描述。画像可能透露研究意图。完整边界见 [隐私与配置](docs/PRIVACY.md)，实际命令见 [工作流](references/workflow.md)。

## 源码与研究项目配合

Git 仓库维护源码、测试和通用文档。研究线放在自己的项目或 iCloud 目录，通过显式 `LINE` 路径使用。已有 `paper-radar-data` 不必改名或搬迁，候选、画像与已推记录沿用原格式。[开发与兼容性](docs/DEVELOPMENT.md)说明旧回执和数据的兼容边界。

## 开发与后续验收

```bash
python3 -m unittest discover -s tests -v
python3 scripts/check_repository.py
python3 scripts/package_skill.py --profile runtime --output ../bear-sift-dev.tar.gz
```

仓库的 GitHub Actions 仅配置离线检查，尚未在线执行。真实服务测试、新用户验收和公开发布由后续维护者按 [验收计划](docs/ACCEPTANCE.md)执行。参与开发先读 [CONTRIBUTING.md](CONTRIBUTING.md)；Agent 接手先读 [AGENTS.md](AGENTS.md)。

## 许可

采用 [PolyForm Noncommercial 1.0.0](LICENSE)，源码可用，允许许可证规定的非商业用途。商业使用需要另行取得权利人的授权。它不是 OSI 定义下的开源许可证。个人非商业研究、学习，以及许可证列举的教育和公共研究等机构用途有明确许可；机构条款不因资金来源而失效，因此不能简单理解成“只要有企业资助就禁止”。具体以许可证原文为准。[许可与第三方内容](NOTICE.md)说明适用范围。
