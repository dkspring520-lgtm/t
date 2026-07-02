# mattpocock/skills：你该学的 5 个核心思维

## 1. diagnose-bugs（不可跳过）
- **先建反馈循环，再读代码**
  - 顺序：tight loop → reproduce → minimise → hypothesise → instrument → fix → cleanup
  - loop 要满足：red-capable、deterministic、fast、agent-runnable
  - 没有 loop 之前，禁止 hypothesise
- **多假说、单变量**
  - 每次只改一个变量，日志加唯一前缀 `[DEBUG-xxx]`，结束时一次 grep 删掉
- **非确定性 bug 的目标是提高复现率**，不是追求“干净复现”

## 2. codebase-design（通用架构语言）
- **Depth 是接口的属性，不是实现的行数**
  - Deep module = 小接口 + 大实现
  - Shallow module = 大接口 + 小实现（要避免）
- **Seam（接缝）**：不改这处就能改行为的位置
- **One adapter = hypothetical seam；Two adapters = real seam**
- **接口即测试面**：想测接口后面，说明模块形状错了

## 3. improve-codebase-architecture（工程评审）
- 先读 `CONTEXT.md` 和 ADR，再 exploration
- 用 deletion test 判断 shallow vs deep
- 输出 HTML 视觉报告（Tailwind + Mermaid），每个 candidate 给 before/after 图
- 评审完后问：要不要 drill down？记录 ADR 冲突要标注清晰度

## 4. implement（执行原则）
- 基于 PRD/issue 实现，能 TDD 就 TDD
- 规律跑 typecheck + 单测，最后跑全量
- 做完先 review 再 commit

## 5. tdd + review + resolving-merge-conflicts（配套工艺）
- TDD：在 correct seam 写 regression test，test 先红后绿
- Review：系统性检查（接口、depth、locality、seam 职责）
- Merge conflict：按 change intent 而不是行号对齐，保持语义正确

---
对你现有 dabao 监控的直接价值：
- 任何“脚本输出总变乱码/字段飘移”的 bug，先用 diagnose-bugs 的 loop 法
- 把现有 20+ 脚本重构为一个 **deep module**（小接口，复杂逻辑内聚）
- 输出升级成 JSON/Markdown table 时，用 codebase-design 的接口设计原则
