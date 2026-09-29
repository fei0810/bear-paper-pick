# Bear Sift 开发入口

继续开发先读 [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) 的当前状态、已确认决定和下一步。运行 Skill 读 [SKILL.md](SKILL.md)；执行跨 Agent 新用户验收读 [docs/ACCEPTANCE.md](docs/ACCEPTANCE.md)。本机研究项目路径若已配置，保存在被 Git 忽略的 `.local-workspace.md`。

Jev 是产品主流程的核心筛选引擎。保持默认 review、可追溯输入、候选与画像绑定，以及分歧采用说明。基础模式须显式选择并标注原因。排序分只描述研究匹配，不可声称科学质量或未经评估的效果收益。

公开维护内容与用户运行数据分开。保持旧画像、候选、已推与缓存标识的兼容；改变格式时同时说明迁移与回退。旧名称的 schema 和问题集标识是兼容契约，不做全局字符串替换。

代码变更复用 `python3 -m unittest discover -s tests -v` 与 `python3 scripts/check_repository.py`。指令变更还需检验真实 Agent 行为；记录宿主与版本、输入、产出、失败和复测。本地回归、在线服务和新用户验收分别报告。

当前用户要求只建本地仓库，未授权本轮创建远程仓库或公开发布。后续有明确新授权时以新授权为准。文档用完整段落组织，维护当前决定，历史事实只放在开发记录中，不让旧说明覆盖当前行为。
