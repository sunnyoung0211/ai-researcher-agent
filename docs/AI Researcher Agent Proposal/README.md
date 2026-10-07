# AI Researcher Agent — Group Project Proposal

本目录为独立 Proposal，原模板未修改。英文正文保留模板的标题、课程信息、Administrative Information、Project Summary、六个正文章节和 Comparisons and Ablations 子节。

- `main.tex`：正文与排版入口。
- `references.bib`：参考文献。
- `main.pdf`：编译后的预览。

组号、成员姓名/学号、仓库链接的宏位于 `main.tex` 开头，目前均为空。具体演示任务、数据/工作负载、模型配置和预算尚未选定，正文没有填入虚构选择。研究设计和评价次数以提议形式描述。

在本目录执行：

```sh
latexmk -pdf -interaction=nonstopmode -halt-on-error main.tex
```

或依次运行 `pdflatex main`、`bibtex main`，再运行两次 `pdflatex main`。

参考文献的作者、题名、年份及相关工作中的概括已对照 arXiv 摘要页核验（2026-09-25）；本稿不声称完成了最新工作的全面综述。
