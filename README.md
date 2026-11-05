# 论文雷达 · PaperDesk

本地运行的文献发现工作台。输入一次研究主题，自动并发检索、汇总去重、按主题相关性排序。无需账号或模型 API Key。

## 使用

双击上层目录的 **启动论文雷达.bat**，或本目录的 `start.bat`。

默认地址：<http://127.0.0.1:8766>。启动脚本自动打开浏览器；重复启动会打开同一个服务。

关闭浏览器不停止服务。使用 **停止论文雷达.bat**，或 `stop.bat` 停止。

1. 输入研究主题或论文标题，点击“开始检索”。
2. 来源陆续返回，列表自动更新；可选择年份、期刊（多选）、综述意图、排序和开放 PDF 筛选。
3. 点击书签收藏，点击“原文入口”查看来源页面；有公开 PDF 链接时可直接下载。
4. 选择结果后导出 RIS、BibTeX、CSV；未选择时导出当前筛选结果。RIS 可导入 Zotero、EndNote。
5. 最近检索可重新打开，收藏和搜索结果保存在本机 SQLite 中。

不打开浏览器、改变端口：

```powershell
.\start.bat -NoBrowser -Port 8767
.\stop.bat -Port 8767
```

## 数据源与实际覆盖

### 期刊筛选

点击搜索框下方的 **期刊筛选**，搜索刊名、中文别名、缩写（如 JACS、TPAMI），或按学科浏览。预置 **727 本中外期刊、20 个学科类别**，包含综合科学、医学、生命科学、化学、材料、能源环境、计算机、通信控制、工程、物理、数学、地学、农业、药学、心理、经济管理和人文社科等。

- 支持跨学科多选、全选当前列表、清空和逐个移除；多选按“任意一本”匹配。
- 未收录的期刊可补充官方完整刊名。目录是常用期刊预设，不是全球全部期刊清单，也不代表分区、影响因子或数据库收录承诺。
- **应用筛选后点击“开始检索”生效**；打开历史记录会恢复该次检索的期刊条件。未选择时不限期刊。
- 条件发送至 Crossref、OpenAlex、PubMed、Europe PMC；再按规范化完整刊名、已配置别名或 ISSN 核验结果，不使用刊名子串匹配。Nature 不自动包含 Nature Medicine 等子刊。
- OpenAlex 按准确匹配的期刊索引 ID 限定；Crossref 使用期刊名 / ISSN 条件；PubMed、Europe PMC 使用期刊字段。期刊较多时分批请求、合并去重。
- 选定期刊后，arXiv 和 Semantic Scholar 因当前接口缺少准确期刊筛选而显示“已跳过”。中文期刊题录仍取决于上述公开来源的实际覆盖，未增加知网、万方自动采集。
- 每批每来源最多获取 100 篇候选，多选不保证每本都有结果，也不是全量检索。

维护目录：编辑 `journal_catalog.py`，每行 `官方刊名|别名1;别名2`；`journal_identifiers.json` 保存从 Crossref 期刊元数据核对的 ISSN。重启服务后生效。目录参考 [Nature 期刊索引](https://www.nature.com/siteindex)、[IEEE 期刊目录](https://open.ieee.org/wp-content/uploads/IEEE-Title-List-December-2025.pdf) 及常见学科期刊名称，不是权威“主流期刊”排名。

### 检索来源

自动连接：**Crossref、arXiv、Europe PMC、PubMed、OpenAlex、Semantic Scholar**。

- 不限期刊时每个来源最多获取 35 篇候选；选定期刊时支持的来源每批最多 100 篇候选。这是面向日常发现的工具，不是数据库全量下载或系统综述的穷尽检索工具。
- 结果按 DOI 和规范化标题合并，补全较长摘要和 PDF 链接；引用次数采用各来源中的最大值，数值口径可能不同。
- 相关性排序基于查询词在标题、摘要中的覆盖及稀有程度，不使用生成模型，也不编造论文、摘要和相关度解释。
- 支持原始中文和英文关键词；少量常见中文学科术语可按内置词典转换为英文。**不是通用机器翻译**；复杂课题和未收录术语可直接输入英文关键词。
- “综述”意图会加入 review 查询词，PubMed / Europe PMC 额外使用综述类型查询；不会保证所有来源的结果都是综述。
- 各来源独立返回，支持限流重试，界面明确显示错误；有源不可用时仍可使用其它结果。无摘要、引用信息或 PDF 链接时会显示缺失状态。
- 来源可能限流、要求授权或因网络不可达而失败。来源返回数量并非平台的总命中数量。

Google Scholar、百度学术、知网、万方、Web of Science、Scopus、IEEE、ACM 提供**外部检索入口**，不宣称已自动采集这些数据库。支持关键词直达的入口携带当前主题，其他平台需要站内粘贴。机构授权和付费全文不由此工具提供。

## 本地文件

- `data/library.sqlite3`：收藏、题录、完整检索记录。
- `data/papers/`：下载的开放 PDF。
- `.cache/`：启动进程记录和日志。
- `.venv/`：Python 独立环境。

备份请停止服务后复制整个 `data/` 目录。程序仅监听 `127.0.0.1`。检索会向公开学术来源发送查询词，PDF 下载会访问来源给出的公开地址。

## 开发与迁移

Python 3.12+、Windows PowerShell，依赖仅 `requests`。

```powershell
uv venv --python 3.13 .venv
uv pip install --python .venv\Scripts\python.exe --index-url https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe app.py
```

前端使用原生 HTML / CSS / JavaScript，无需 Node 构建和在线字体。迁移电脑后重新创建 `.venv`。
