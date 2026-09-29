# 安装自检与验收边界

源码回归使用 `python3 -m unittest discover -s tests -v` 和 `python3 scripts/check_repository.py`。运行包只含运行所需文件，不含 tests；需要离线回归时使用源码或审阅包。

实际安装与新用户验收按 [验收计划](../docs/ACCEPTANCE.md)执行，当前未完成 Claude Code 或其他宿主的新用户验收。已有真实配置的维护者如要检查 Jev，先 plan 核对外发，再对公开样本限定请求数量。没有 key 的默认 Jev 流程应明确报错，只有显式基础模式可继续而不调用服务。

Windows 当前要求串行写状态。操作系统和宿主未实测时应标明未验证，不从标准库兼容性推断用户流程已经通过。
