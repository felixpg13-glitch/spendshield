---
description: Run the full SpendShield test suite
---

Run the complete test suite and report results:

```bash
python3 -m pytest tests/ -q
```

Report: total passed/failed. If any test fails, investigate and fix before proceeding. 任何改动必须保持全绿(迭代 5 问最后一条)。
