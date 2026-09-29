# API 手册与已知数据坑（手动路径）

`scripts/fetch.py` 可用时不读本文件。仅当脚本未覆盖某项核验时，按以下 curl 路径补查。没有 Python 3 时可以做一次性人工检索，但不能维护 `candidates.json` / `seen.json`，因此不得把结果称为可持续的 weekly 运行。

**手动路径的转写纪律**：先把 curl 输出重定向保存为 .json 文件；脚本可用时再由脚本生成候选记录。禁止看着屏幕手抄标题。OpenAlex 是候选发现源，不是身份与文本主张的唯一裁判：版本、异常日期、标题与摘要明显不一致、以及正文级判断，必须以 DOI 落地页、出版方或原文复核。

## curl 路径

- 定位单篇：`https://api.openalex.org/works/doi:10.1038/xxxx`（或 `/works/W123…`）
- 检索：`https://api.openalex.org/works?search=关键词&filter=from_publication_date:2026-06-01&per-page=25`
- 施引：`https://api.openalex.org/works?filter=cites:W1234567890,from_publication_date:2026-06-01&sort=publication_date:desc`
- 批量解析（经典层/多版本并集）：`https://api.openalex.org/works?filter=ids.openalex:W1|W2|W3&per-page=100`（管道符为 OR，实测可用）
- 施引反推正典（canon 手动版）：查种子的施引文献（cites 过滤+时间窗，per-page 100）→ 逐篇取 referenced_works 计频（排除种子自身）→ 取频次 top100 批量 ids.openalex 解析 → 按频次排序
- 作者：`https://api.openalex.org/authors?search=姓名` 拿 author.id，再 `works?filter=author.id:Axxxx,from_publication_date:2026-06-01`
- 摘要手动还原（倒排索引）：`python3 -c "import json,sys; inv=json.load(sys.stdin); pos={i:w for w,ix in inv.items() for i in ix}; print(' '.join(pos[i] for i in sorted(pos)))"`
S2 推荐：当前脚本采用单篇 `GET /recommendations/v1/papers/forpaper/<id>`，有 key 才启用。本接口不使用负样本。此前以 GET 查询参数 `positivePaperList` 得到 404，不能据此断定正确的 POST 列表接口不可用；`POST /recommendations/v1/papers/` 的正负样本调用留待配置 key 后单独验证，当前不宣称已支持。
- S2 单篇补摘要：`https://api.semanticscholar.org/graph/v1/paper/DOI:10.x?fields=title,abstract,venue,year`

## 历史观测与当前处理

下列观测来自前期测试，ID、计数和映射可能随上游修订。它们支持复核边界，不是当前每次运行都成立的事实。

1. **引文边有真实缺口**。BERT 明确引用 Transformer，但 OpenAlex 的 BERT 记录（W2963341956，52 条 refs）没有这条边。任何单通道不可全信，多通道首先是容错。
2. **著名论文记录碎片化**。Transformer canonical 记录 W2626778328 挂 2025 年重发表 DOI（10.65215/2q58a426），cited_by 仅 7546，巨量历史引用在别的记录。定位种子后查 `locations` 与 `related_works`，多版本 ID 引文并集、年份取最早。
3. **参考文献抽取稀疏**。两粒种子的参考文献共引可为 0（Transformer+BERT 实测）。种子较少时，共引缺失不足以证明论文不重要；种子少时退化为"单种子 refs 按全时段被引排序"。
4. **作者消歧不可省**。搜索 James Zou 返回两个同名 ID（A5005779176 / A5123661483）。入关注名单前核对近作主题 + 记录单位。
5. **S2 调用方式需要区分**。当前已实现通道无 key 时跳过；路径式不使用负样本，不代表官方服务全局不支持负样本。
6. **OpenAlex search 是 AND 语义，跨域类比词会被同名旧领域淹没**。实测"software engineering agents"类词命中 1.8 万条、大量 1990s 文献。当前使用 search=，按画像加入精确短语、布尔组合与领域限定；不把 LLM 等词作为跨学科通用条件。
7. **OpenAlex 的 arXiv→作者归属滞后**。实测：Zou 2026 年作者档案 19 篇里缺 Fisher-R1、Agentic Garden of Forking Paths、Physics of Agents 三篇 arXiv 预印本——authorworks 通道对同组新作结构性失明。对策：有 S2 key 时可用 graph 端点按作者补查（`/graph/v1/author/{author_id}/papers?fields=...`），或在战报注明该盲区由用户侧补充
8. **无时间窗检索 + cited_by 排序会淹没新研究**。跨域层按被引排序时前排全是旧经典与综述（W38b 实测：换词后前排仍是 Geant4、AutoCodeRover 之前的高被引综述）。对策：跨域定向检索改用系统专名（如 SWE-bench、UniAD）而非泛方法词
9. **arXiv DOI 映射存在标题污染**。实测：W4387561453 挂着 arXiv:2310.06770（SWE-bench）的 DOI，display_name 却是另一标题。凡经 arXiv DOI 定位的记录，标题与 arXiv 原文核对后才可用
10. **related_works 不是版本注册表**。实测：rentosertib 的 10 条 related_works 无一是版本记录，混入无关 RCT。多版本合并必须逐条核对标题，不能信 related_works 字段
11. **机构映射噪声（ROR 错配）**。实测（2026-09-19 deep-read 回测）：FutureHouse 被映射为"Institute for the Future"（另一家智库），Edison Scientific 被映射为"Edison Pharmaceuticals"。人物/行业层引用机构字段前需人工复核公司名
12. **作者 ID 碎片化不止同名消歧**。实测：Andrew White（ChemCrow 作者）在 Robin Nature 版上挂的新 ID（A5136489402）仅存 1 篇；Brenner 的 ERA 关联 ID（A5104321680）只有 11 篇。资深作者档案篇数异常少时，提示 ID 碎片而非低产
13. **社论/新闻可机查配对**。Nature 系社论有独立 `type=editorial`，同日同刊检索（primary_location.source.issn + publication_date）可同时捞出论文、社论、新闻、评论——事件检测信号 ①② 实测一次查询完成（Nature 2026-05-19 共 19 条）
14. **doi:10.48550/arxiv.XXXX 直查 404**。arXiv 预印本若已被合并进期刊版记录，用 arXiv DOI 反查会 404；从期刊版记录的 locations 里取 arXiv 链接才是正道（实测 Co-Scientist/ERA 均如此）

> 维护：每次 API 或数据模式变化后，更新相应命令和失效模式，并重跑 self-test。变更前先核对当前文件与待发布副本，避免覆盖其他已验证修订。

当前接口依据：[OpenAlex 检索](https://help.openalex.org/api/searching/) 与 [认证](https://help.openalex.org/api/authentication/)。本版使用可选 Bearer key，日志和原始 URL 不写密钥；不再使用占位 mailto。
