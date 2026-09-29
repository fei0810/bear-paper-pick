# 参与开发

先读 [开发状态](docs/DEVELOPMENT.md)，从一个可复现的问题开始。记录输入、预期行为和实际结果，在独立分支中完成相应修改。合并前运行离线检查，并对实际受影响的行为保留回归；修改 Skill 指令需要真实 Agent 行为证据，不能用格式检查替代。

```bash
python3 -m unittest discover -s tests -v
python3 scripts/check_repository.py
```

测试不需要 API key。真实 Jev 调用应明确使用公开样本、模型版本与请求上限。用户画像和原始运行日志留在自己的项目目录；提交 Issue 时只提供最小脱敏复现，不粘贴 `.env` 或完整私人研究线。

一项变更同时更新对应说明和 CHANGELOG。新 CLI 行为或格式需要迁移说明；基础机械错误用离线测试覆盖，筛选质量用冻结样本与独立留出集比较。版本号集中在 VERSION，开发快照不打正式发布标签。按照 [新用户验收](docs/ACCEPTANCE.md)完成后再准备 Release。

项目采用 [Apache License 2.0](LICENSE)（`Apache-2.0`）。除明确另行说明或有单独约定外，有意提交并纳入本项目的贡献按该许可证提供。请确认你有权提交这些内容；第三方代码保留原许可与归属，不能把不兼容来源重新标为本项目许可。
