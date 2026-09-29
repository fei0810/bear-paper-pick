# 解读包：Nature 同日三篇 AI4S 论文事件 · 2026-05-19

> 结构样例，不是可直接复用的事实报告。本文件含未附原始链接的叙事性断言，事实均未按证据台账核对；不得把其中任何事实、数值、判断或引用直接用于对外输出。每次解读必须重新取证，并按 deep-read.md 增补“证据台账”。标注：⚖️=承重（影响价值判断）·=背景。来源分级：官方 / 自报 / 民间。

## TL;DR

1. 2026-05-19 Nature 同日上线三篇 AI 科研 agent 论文（Google Co-Scientist / FutureHouse Robin / Google ERA）+ 一篇社论 + 一篇新闻 + 两篇同日批评声音；同日 Edison Scientific 官宣与药企 Incyte 合作——多方共同编排的事件，不是巧合。
2. 三篇代表三种"发现"：从文献推断未发表结论（已被实验验证）、已有知识的跨域组合（临床前）、可打分空间寻优（指标层面）。审稿文件暴露了各自的承重弱点（见学术层）。
3. 人物线高度集中于 Google Med-PaLM 血统；商业化主线在 FutureHouse→Edison（$70M 融资 + Incyte）。

## 事件时间线

| 日期 | 事件 | 来源 |
|---|---|---|
| 2025-02-19 | Co-Scientist 预印本 arXiv:2502.18864 与 Penadés 验证预印本 bioRxiv 10.1101/2025.02.19.639094 **同日上线** | 官方 |
| 2025-05-19 | Robin 预印本上线（arXiv，OpenAlex W4417287919） | 官方 |
| 2025-09-09 | 验证论文登 Cell（W4414152979，online 日期；用户记忆为 11 月底，见存疑） | 官方 |
| 2025-09 | ERA 预印本 arXiv:2509.06503 | 官方 |
| 2025-11-05 | FutureHouse 发布 AI scientist **Kosmos** 并拆分商业公司 Edison Scientific（endpoints.news 独家） | 媒体 |
| 2025-12-18 | Edison 融资 **$70M**（Bloomberg 等） | 媒体 |
| 2026-05-19 | **Nature 三篇齐发** + 社论《Why AI cannot do good science without humans》+ 新闻（Heidi Ledford）+ 批评文两篇；**同日 Edison 官宣 Incyte 合作**（Kosmos 平台进药企 R&D）；Google I/O 2026 | 官方+媒体 |
| 2026-06-29 | Edison × Population Health Partners（STAT：Metsera 班底造新药公司） | 媒体 |

## 五层原料

### 学术层

- ⚖️ Co-Scientist《Accelerating scientific discovery with Co-Scientist》Nature 2026-05-19，DOI 10.1038/s41586-026-10644-y，被引 93。基于 Gemini 的多 agent 假说生成；验证策略是"闭卷考"——拿 Penadés 团队刚实验证实、外界未知的 cf-PICIs 机制当考题，排名第一的假说与实验结论吻合（劫持噬菌体尾部扩宿主范围）。**无公开审稿文件**（另两篇有）。
- ⚖️ Robin《A multi-agent system for automating scientific discovery》Nature 2026-05-19，DOI 10.1038/s41586-026-10652-y，被引 45。Crow/Falcon（文献）+ Finch（数据分析）闭环，dAMD 用例：第一轮命中 ROCK 抑制剂 **Y-27632**，第二轮迭代才提出 ripasudil（日本已批青光眼药），Finch 独立发现 ABCA1 上调。审稿文件（公开，MOESM4）Referee #1 列 12 条批评：**AI 报告的 7.5 倍 MFI 与人分析 1.75 倍不一致**（管道稳定性存疑）；"首个全自动系统"被点名收窄（引 Co-Scientist、Coscientist 2023、TxGemma）；ROCK 机制新颖性"incremental"（Y-27632 已有前人工作 Mao & Finnemann 2021）；人力投入未量化。
- ⚖️ ERA《An AI system to help scientists write expert-level empirical software》Nature 2026-05-19，DOI 10.1038/s41586-026-10658-6，被引 0（新记录）。LLM + tree search（置信界策略）重写科学代码，scRNA-seq 批次整合 87 配置中 40 超 OpenProblems 已发表方法；调用了 Gemini Deep Research 和 Co-Scientist 生成思路。审稿文件（公开，MOESM2）承重批评：**只开源了产物代码、优化过程代码未开源**，审稿人直指不符合 Nature 材料公开标准；GIFT-Eval 用的是 2025-05-18 榜单快照（漏 TiRex/Toto/Timecopilot）；与 LLM 之前的遗传编程谱系未充分对账。
- · 三篇摘要与评测声称的原文数字已核录（raw/ 下 JSON）；预印本-正式版并集完成（Co-Scientist/ERA 的版本合并在同一 OpenAlex 记录内）。
- · Peer review 可得性：Robin ✅ ERA ✅ Co-Scientist ❌（用户"有条件必读"的"条件"实测即此——是否公开审稿文件）。

### 人物层

- ⚖️ **Vivek Natarajan**（A5103234563，Co-Scientist 末位通讯，h=20）：Med-PaLM（Nature，3834 被引）→ Med-PaLM 2 → Med-Gemini 血统；**一人横跨簇内两篇**——同时是 Penadés 验证论文的通讯作者。"主推作者"信号（横跨多篇+方向连续性）实测兑现。2026-06 有新作"AI Co-Data-Scientist"（可穿戴生物标志物）。
- ⚖️ **Samuel Rodriques**（A5061828831，Robin 末位通讯，h=15）：履历 Slide-seq 发明人（2664 被引，2019）→ Broad/MIT → FutureHouse 创始人；机构字段同时挂出 Edison（映射噪声，见存疑）。2026 近作 BixBench3（agent 基准）。
- ⚖️ **Michael Brenner**（A5104321680，ERA 末位通讯）：Harvard 应用数学 → Google Research；近作轨迹是一条 benchmark 线（CMT-Benchmark 2025-10 → 专家级学术问题基准 2026-01 → ERA 2026-05）——先造尺子再造系统。
- · Juraj Gottweis（Co-Scientist 一作，Google 瑞士）：同 Med-PaLM 血统。Eser Aygün（ERA 一作）：强化学习出身（Option Keyboard），tree search 的 RL 血缘。Andrew White（Robin 通讯）：ChemCrow 作者，但 OpenAlex 此 ID 碎片化（仅存 1 篇）。Penadés（验证论文一作+通讯，Imperial College）：cf-PICI 十年深耕方。

### 行业层

- ⚖️ **Edison Scientific**（FutureHouse 商业分支）：2025-11-05 分拆 + Kosmos 发布（endpoints 独家，自称"几小时做完六个月的研究"）；2025-12-18 融 $70M（Bloomberg）；**2026-05-19 与 Nature 论文同日官宣 Incyte 战略合作**（Kosmos 进药企研发全流程，Business Wire/Fierce Biotech 等六家媒体）；2026-06-29 与 Population Health Partners 合作（STAT：Metsera 班底）。
- ⚖️ 用户原帖的"6 家 Top10 药企主动来信"是 FutureHouse 博客**自报**信源；Incyte 合作是首个有名有姓、可独立佐证的落地（且日期与论文同天——档期编排证据）。
- ⚖️ ClinicalTrials.gov 检索 ripasudil × macular degeneration：**无注册临床试验**（2026-09-19 查）——从论文到临床的下一棒尚未开跑，Nature 新闻里外部专家的谨慎有实证支撑。
- · Google 侧无独立商业化动作（内部产品化路线，I/O 展示）；Kosmos 与 Robin 的技术血缘关系未公开说明。
- · 数据源说明：OpenAlex funders 字段实测为空，行业层全靠新闻 RSS；临床试验库仅覆盖注册试验。

### 叙事与反响层

- ⚖️ 配对检测：同刊同日 19 篇记录里命中三篇画像论文 + 社论（type=editorial 可机查）+ Ledford 新闻——"论文+社论同日配对"信号实测成立。
- ⚖️ **同日还有两篇批评声音**（用户原帖未提）：Messeri & Crockett 评论《The uncritical adoption of AI in science is alarming》（d41586-026-01557-x）；Mikula 来信《AI might jeopardize the uncertainty required in science》（d41586-026-01605-5）。Nature 的"刹车"不止社论一脚。
- ⚖️ 档期政治：预印本两对同日（2025-02-19 双预印本；Robin 2025-05-19 与 Nature 2026-05-19 正好一周年）；论文、社论、批评、药企公告全部压在 2026-05-19——多方的注意力合谋。
- · 民间反响：用户原帖记载 X 上"Robin 算不算 discovery"两派交锋（机器未取到具体推文，需人工补 URL；The Conversation 2026-05-20《AI scientists are improving – but reveal their fundamental limits》可作书面化对应）。
- · Robin 数字出入：审稿文件写"约 400 篇文献"，用户原帖写 551 篇/30 分钟——版本差异或记忆偏差，存疑待核（见存疑）。

### 实操层（条件层）

- · Co-Scientist：封闭申请制（用户亲历申请无回音）→ **L0**。
- ⚖️ ERA：审稿文件证实优化过程代码未开源，GitHub 检索无官方仓库 → **L0**，且是"不可复现的 Nature 论文"争议点。
- · Robin/Kosmos：商业平台未开源 → **L0**；其组件 PaperQA 开源（PyPI），本机安装因 iSH 环境性能超时未完成（420s），未判定不可装，需桌面环境复测 → **L1 未竟**。

## 候选坐标轴（机器草稿，供挑选/改写）

1. **发现的认识论类型**：文献推断未发表知识（Co-Scientist）／已有知识的跨域组合（Robin）／可打分空间寻优（ERA）——按"与真相的距离"排序。
2. **验证闭环的位置**：预先知道答案的闭卷考（Co-Scientist）／实验在环中迭代（Robin lab-in-the-loop）／自动指标评分（ERA）——人把守证据的位置不同，风险敞口也不同。
3. **可复现性与开放度**：审稿文件公开且全栈可查（Robin）／审稿公开但核心代码未开（ERA）／审稿不公开且系统封闭（Co-Scientist）——与各自声称的野心恰好成反比。

## 扣子候选

1. ripasudil 的 7.5x（AI）vs 1.75x（人）不一致，作者在 rebuttal 里如何回应？（审稿文件 2300 行可查）——升级为：何时进临床（目前无注册试验）。
2. ERA 优化代码未开源却过了 Nature 的栏——"不可复现"先例会不会被后来者引用？
3. Incyte 合作与 Nature 论文同日官宣是谁主导的档期？三篇同发 + 社论 + 批评 + 药企公告的编排成本由谁承担、收益归谁？
4. Kosmos 相对 Robin 改了什么？产品线血缘未公开——Edison 的技术叙事与论文学术叙事是否同源？
5. 开放式科学问题（不可打分）的假说评价怎么做？（Co-Scientist 用 LLM 裁判、Robin 用实验、ERA 用排行榜——三篇都没回答一般情形。）
6. Natarajan 的 Med-PaLM 血统到 AI scientist 的路线连续性：医学考试题答完之后为什么选"科学发现"？（对应用户原帖的"主推"扣子。）

## 存疑与没看懂

- OpenAlex 作者 ID 碎片化：White / Brenner / Ghareeb 的档案不全（White 此 ID 仅存 1 篇）——人物层对这三人的结论只覆盖局部。
- 机构映射噪声：FutureHouse → "Institute for the Future"（另一家智库）；Edison Scientific → "Edison Pharmaceuticals"。机构字段引用前需人工复核。
- Cell 版日期：OpenAlex 记 2025-09-09（online），用户记忆 2025-11 底——可能为刊期 vs 在线差，未核。
- Robin 文献数 400（审稿文件）vs 551（用户原帖）——未核。
- X 两派交锋无机器取证（缺推文 URL）。
- Co-Scientist 为何无公开审稿文件：未选择透明评审？待查。
- paper-qa 本机安装超时是 iSH 性能问题，不构成对工具本身的判断。

## 层反馈区

学术 ｜人物 ｜行业 ｜叙事反响 ｜实操 —— 每层标 有用/噪声/缺失（反馈落账进 profile.md「解读层权重」）
