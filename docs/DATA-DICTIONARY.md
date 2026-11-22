# 数据字典

## 输入清册

输入为 UTF-8 JSON 对象，字段 `rules`、`titles`、`receipts` 分别为三张数组。对应单表上限 3000 行，生成应到期位上限 10000。原始行号从 `receipts` 首个数组项起算，首项为 1。

| 表 | 字段 | 定义 |
|---|---|---|
| 订购 | `chain` | 稳定订阅链字符串，区分大小写 |
| 订购 | `from_year`, `to_year` | 双端包含，整数 1900–2200，单段不超过 12 年 |
| 订购 | `first_volume` | 首年卷号，随后每年加一 |
| 订购 | `issues_per_year` | 单年正刊期数 1–60 |
| 订购 | `numbering`, `first_issue` | `yearly` 按年从 1 起，`continuous` 按出版年度累加，首年首期可指定 |
| 刊名 | `chain`, `title`, `from_year`, `to_year` | 生效年度包含两端，规范化后同刊名每年只有一个映射，同链可登记同年的多个别名 |
| 来刊 | `accession` | 物理件验收号，以字符串原样保留前导零，去除两端空格后参与去重 |
| 来刊 | `title`, `year` | 来刊登记刊名和整数出版年，年度按出版年核对 |
| 来刊 | `volume`, `issue` | 字符串，允许全角与前导零，卷期为 1–9999 |
| 来刊 | `kind` | `regular` 正刊默认值，`supplement` 增刊单列 |

期号范围可使用 `-`、`–`、`—`、`~` 或 `至`，包含两端。来刊以验收号分组，同组相同字段视为重放，不同字段保留独立候选。`null`、空字串或空对象记空行，其他坏字段给出字段名并随原值隔离。不使用到达顺序推断卷期。

## 期位核验

期位键为 `(chain, year, issue)`。卷号由订购规则唯一决定，用于来刊交叉检查。相邻订购段须递增接续卷号，两段都连排时还须接续期号。合期只认合法年度内的完整范围。每个期位通过所有候选确定状态，分为覆盖、缺刊、争议。争议包括多个件号覆盖和同件号不同来刊描述。两种争议均可从到藏记录追溯原始清册。

## 调用与归档

各阶段接受 `GET /api/<阶段>/<clean|dirty>` 或 `POST /api/<阶段>`。请求体用 `{"sample":"dirty"}` 或 `{"data":{...}}`，两个来源只能选一个。阶段名为 `orders`、`receipts`、`numbering`、`combined`、`titles`、`balance`。

`POST /api/runs` 接受同一请求并保存运行。运行编号为规则版本与规范 JSON 输入摘要的 SHA-256。`GET /api/runs/{id}` 读归档，`POST /api/runs/{id}/rerun` 用归档里的原输入再算。同摘要的重复归档保留首份运行时间与结果。规则版本变化会生成新编号。`GET /api/runs/{id}/export` 下载一份 JSON 凭证，含原始清册和计算证据，归档时间用 UTC。

## 签收与补录修订

`POST /api/runs/{id}/sign` 以 `{"signer":"…","note":"…"}` 签收一份原始勾稽版本。签收时间只取首次，重复签收返回原签收时间。签收只落于原始版本；对修订版签收返回 `SIGN_BASELINE_ONLY`。

`POST /api/runs/{id}/revisions` 接受 `{"receipts":[…],"note":"…"}`，`receipts` 使用与来刊表相同的字段。请求可发往签收版或任一修订版，均登记到同一签收基线之上。补录行一律追加在现行清册之后，不删除、不改写签收原件：

| 补录归因 | 判定 | 说明 |
|---|---|---|
| `LATE` | 验收号未在上一版出现 | 迟到原件，可补齐缺刊 |
| `CORRECTION` | 验收号已登记但来刊描述不同 | 对签收/已登记原件的勘误，与原件并列并可能转为争议 |
| `REPLAY` | 验收号与描述均与上一版相同 | 补录重放，按重复回传计数，不新增覆盖 |
| `ISOLATED` | 空行或字段无效 | 保留原始位置，记入隔离 |

修订编号为规则版本、基线编号与补录规范 JSON 摘要的 SHA-256。同一补录对同一基线重复提交命中既有修订（`reused` 为真），不新增覆盖也不重复追加来刊行。修订文档含 `baseline_manifest`、`baseline_signature`、`supplement`（原件与摘要）、`supplement_audit`（逐行归因）、`delta`（对照上一版）与 `baseline_delta`（对照签收版）；每个变更期位给出 `reason`、`before`/`after` 状态与带 `origin`（`baseline`/`prior`/`supplement`）的原件行。`GET /api/runs/{id}/revisions` 列出基线与各修订版及现行版本。修订版重跑仍走 `POST /api/runs/{id}/rerun`，按同一补录幂等重建。
