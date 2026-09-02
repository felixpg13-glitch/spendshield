---
description: 七步发布流程 (PyPI + GitHub)
---

按以下顺序执行发布(每次发版走完整七步):
1. bump pyproject.toml + spendshield/__init__.py 版本号(两处一致)
2. 全量测试: `python3 -m pytest tests/ -q` 全绿
3. 构建: `rm -rf dist build && python3 -m build`
4. 干净 venv 验证 wheel(import + enforce + __version__ 正确)
5. twine upload(PyPI token 在 macOS 钥匙串 pypi-token: `security find-generic-password -s pypi-token -w`)
6. git commit + tag vX.Y.Z + push(走代理: `git -c http.proxy=http://127.0.0.1:6514 push origin main --tags`)
7. 从 PyPI 真实安装验证
⚠️ 发布前自问: 这次改动有没有给攻击者增加一条花钱路径?(PR 模板第 5 问)
